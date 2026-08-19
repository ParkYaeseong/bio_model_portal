# Portal MCP Server — Design (SP2)

Date: 2026-07-03
Status: Approved (design)
Scope: **SP2 only.** SP3 (in-UI multi-provider chatbot) and SP4 (self-improvement)
are separate specs. SP1 (RAPID workflow) is hidden in the UI (code retained).

## 1. Goal

Let an external AI client (Claude / Codex / Gemini via their MCP client) — and
later the in-UI chatbot (SP3) — **run the portal's models, check status, fetch
results, and cancel**, using the portal's existing gateway + jobs + per-user SSO
identity. Connection uses a one-click Personal Access Token (PAT).

The AI does the explaining; the MCP tools only provide structured data.

## 2. Key decisions (from brainstorming)

- **Portal-native MCP**, not reuse of protein_pipeline's `pipeline-mcp` (that is a
  separate product with separate auth/data). The portal MCP wraps the portal's own
  job flow and user model.
- **Generic `run_model` tool** (pipeline as an argument) rather than one tool per
  model; `list_models` supplies each model's input schema so the tool is
  self-documenting.
- **Transport = hand-rolled MCP JSON-RPC over HTTP** on a FastAPI route (mirroring
  how `pipeline-mcp/http_server.py` does it — `{"jsonrpc":"2.0",...}`). No new MCP
  SDK dependency; fits the portal's FastAPI/httpx stack.
- **Auth = PAT** (`kbfpat_...`, SHA-256 hash stored, raw shown once), reusing the
  established pattern. Real MCP OAuth is deferred (prior decision).
- Tool functions are shared service functions so **SP3's in-UI chatbot calls the
  same code in-process** (logged-in SSO user, no PAT).

### Non-goals (SP2)

- The chatbot UI, multi-provider key handling (all SP3).
- Real OAuth / dynamic client registration (deferred).
- Self-improvement (SP4).
- New model workers — everything routes through the existing gateway.

## 3. Architecture

```
External AI (Claude/Codex/Gemini MCP client)
   │  HTTP JSON-RPC + Authorization: Bearer kbfpat_...
   ▼
Caddy (biomodel.…)  ──► bmp-backend  POST /mcp   (MCP JSON-RPC endpoint)
                                        │  PAT → user
                                        ▼
                          app/mcp/tools.py  (list_models, run_model,
                          job_status, job_result, cancel_job)
                                        │  reuse
                                        ▼
                     existing runpod.py / jobs flow / gateway / storage
                                        │
                                        ▼
                                   GPU workers

SP3 chatbot (later) ── in-process ─► same app/mcp/tools.py (SSO user, no PAT)
```

- The MCP endpoint is a normal FastAPI route on **bmp-backend** (no separate
  service), so it inherits deploy/monitoring. Caddy must route `/mcp` and the PAT
  auth endpoints to the backend **without** the SSO forward_auth redirect (MCP
  clients send a Bearer token, not an SSO cookie) — see §7.

## 4. MCP protocol surface (`POST /mcp`, JSON-RPC 2.0)

Implement the minimum MCP methods:

- `initialize` → `{ protocolVersion, capabilities: { tools: {} }, serverInfo: { name: "bio-model-portal", version } }`.
- `notifications/initialized` → accepted (no-op).
- `tools/list` → the five tool definitions (name, description, `inputSchema` JSON Schema).
- `tools/call` → `{ name, arguments }` → dispatch to the tool function; return
  `{ content: [{ type: "text", text: <JSON string> }], isError? }`.

Unknown method → JSON-RPC error `-32601`. Malformed → `-32700/-32600`. Tool
exceptions → `tools/call` result with `isError: true` and a message (not a
protocol error), so the AI can react.

## 5. Tools

All tools are scoped to the authenticated user; job ownership is enforced.

- **`list_models()`** → `[{ key, label, description, supports_sequence,
  requires_archive, input_fields: [ …from PIPELINES/InputField… ] }]`. Sourced
  from the existing `runpod.PIPELINES` so it always matches the UI.
- **`run_model(pipeline, parameters?, sequence?, files?)`** → validates `pipeline`
  against `PIPELINES`, builds the job the same way the UI submission path does
  (reuse the gateway payload build + `RunpodClient.submit`), creates a portal
  `Job`, returns `{ job_id, status, endpoint_id }`. `files` (optional) are
  base64-encoded inputs for archive pipelines; MVP may support sequence-only
  models first and note archive support.
- **`job_status(job_id)`** → `{ job_id, pipeline, status, error_message }` (owner-checked).
- **`job_result(job_id)`** → `{ status, artifacts: [{ id, file_name, kind, size }],
  metrics?, download_hint }` — enough for the AI to summarize/explain. Artifact
  bytes are not inlined; the AI gets names/kinds + a note on how to download.
- **`cancel_job(job_id)`** → best-effort cancel (owner-checked).

Tool functions live in `app/mcp/tools.py` and take an explicit `user` +
`db` so both the MCP endpoint (PAT-resolved user) and SP3 (SSO user) can call them.

## 6. Auth — Personal Access Tokens (one-click)

- New model `PersonalAccessToken`: `id, user_id, name, token_hash (sha256),
  prefix (first 8 chars for display), created_at, last_used_at, revoked_at`.
- `app/mcp/pat.py`: `create_pat(user, name) -> raw_token` (raw `kbfpat_<random>`
  returned once), `resolve_pat(raw) -> user | None` (hash lookup, not revoked,
  updates last_used_at), `list_pats(user)`, `revoke_pat(user, id)`.
- The `/mcp` endpoint reads `Authorization: Bearer kbfpat_...`, resolves the user,
  and 401s if missing/invalid/revoked.
- **Portal API** (`routers/mcp_tokens.py`, SSO-gated like other portal routes):
  `POST /api/mcp/tokens` (create → raw shown once), `GET /api/mcp/tokens` (list,
  no raw), `POST /api/mcp/tokens/{id}/revoke`.

## 7. Frontend — "AI 연결 (MCP)" settings

A small page/section (e.g. `src/app/mcp/page.tsx` or a modal) that:
- lists existing tokens (name, prefix, created, last used) with revoke,
- "토큰 생성" → shows the raw `kbfpat_...` **once** with copy button,
- shows the **MCP endpoint URL** (`https://biomodel.…/mcp`) and a ready-to-paste
  config snippet for Claude/Codex/Gemini MCP clients,
- brief instructions.

## 8. Caddy / deploy

- `/mcp` and `/api/mcp/tokens*` are backend routes. `/api/mcp/tokens*` stays behind
  SSO forward_auth (portal user creating tokens in-browser). `/mcp` must be
  reachable with **Bearer PAT only** (no SSO cookie) — add a Caddy matcher so
  `/mcp` bypasses `forward_auth` and proxies straight to bmp-backend. (Coordinate
  with the infra host that owns the Caddyfile; document the required block.)
- No new services; bmp-backend gains the routes. Redeploy = restart bmp-backend
  (+ bmp-frontend for the settings page).

## 9. Error handling

- Invalid/unknown pipeline in `run_model` → tool error with the list of valid keys.
- Missing required params → tool error naming them (from InputField.required).
- Not-owner / missing job → tool error (never leak other users' jobs).
- PAT invalid/revoked → HTTP 401 at `/mcp`.

## 10. Testing

- Unit: PAT create/resolve/revoke (hash only stored; revoked rejected).
- MCP protocol: `initialize`, `tools/list` (5 tools with schemas), `tools/call`
  for each tool with a fake gateway/job layer; unknown method → -32601; bad PAT → 401.
- `run_model` reuses the existing submission path (mock `RunpodClient`), asserts a
  `Job` is created and `job_id` returned; ownership enforced on status/result/cancel.
- A live smoke test: create a PAT via the UI, connect one MCP client (or curl the
  JSON-RPC), `list_models` + `run_model` a fast model (e.g. esmfold) end-to-end.

## 11. Deliverables

- `app/mcp/{__init__.py, server.py (JSON-RPC), tools.py, pat.py}`,
  `app/routers/mcp_tokens.py`, `app/models.py` (+PersonalAccessToken),
  `main.py` wiring, tests.
- `frontend/src/app/mcp/*` (token/settings UI) + `api.ts` additions.
- README "AI 연결 (MCP)" section + the required Caddy `/mcp` bypass block.

## 12. SP3 preview (not built here)

The in-UI chatbot will call `app/mcp/tools.py` in-process for the logged-in SSO
user, wrap a multi-provider LLM layer (Claude/OpenAI/Gemini) keyed by a
user-supplied API key (browser-stored, like the esmfold2 key), and let the model
call these tools to run pipelines and explain results.
