# SP3 — In-UI Multi-Provider Execution Chatbot Design

**Goal:** Let a portal user chat with an LLM (using their own API key) that can *run and explain* the portal's models by calling the SP2 MCP tools in-process.

**Architecture:** A new SSO-gated `POST /api/chat` endpoint runs an LLM tool-calling loop server-side. It hands the LLM the 5 MCP tool schemas from `app/mcp/tools.TOOLS`; when the LLM calls a tool, the backend executes the matching `tools.py` function **for the authenticated SSO user** (no PAT) and feeds the result back. Loops until the LLM returns a final text answer. The user's API key is used per-request and never stored/logged.

**Tech Stack:** FastAPI, httpx (raw HTTP, matching existing `assistant.py` OpenAI call — uniform across the three providers), Next.js widget.

---

## Provider abstraction (`app/chat/providers.py`)

One adapter per provider (`anthropic`, `openai`, `gemini`), each exposing a uniform interface the generic loop drives:

- `format_tools(TOOLS)` → provider-native tool/function schema list.
- `build_messages(history)` → provider-native message list from a simple `[{role, content}]` history (`user`/`assistant` text only).
- `request(api_key, model, system, messages, tools)` → raw response dict (raises on HTTP error).
- `parse(resp)` → `{text, tool_calls: [{id, name, arguments}], stop}`.
- `append_assistant(messages, resp)` / `append_tool_results(messages, results)` → mutate the provider message list for the next round.
- `default_model`.

**Anthropic** (priority, live-tested): `POST https://api.anthropic.com/v1/messages`, headers `x-api-key` + `anthropic-version: 2023-06-01`; body `{model, max_tokens, system, tools, messages}`; loop on `tool_use` content blocks, reply from `tool_result` round-trips. Default model `claude-opus-4-8`.

**OpenAI**: chat completions function-calling. **Gemini**: `generateContent` functionCall/functionResponse. Structurally implemented; not live-verified (no key) — documented as such.

## Loop (`app/chat/loop.py`)

`run_chat(db, user, provider_name, api_key, model, history, max_iters=6) -> {reply, tool_calls, model}`:
1. Build tool schemas from `app/mcp/tools.TOOLS`.
2. Round: `request` → `parse`. If no tool calls → return `text` as reply.
3. For each tool call, execute `TOOLS[name][0](db, user, arguments)` in-process; collect `{name, arguments, result}`; feed results back.
4. Cap at `max_iters`; on cap, return the last text (or a note).

Ownership is enforced by `tools.py` (`_owned_job`) since it runs as the SSO user — identical isolation to the MCP path.

## Endpoint (`app/routers/chat.py`)

`POST /api/chat` (SSO-gated via `get_current_user`). Body: `{provider, api_key, model?, messages:[{role,content}]}`. Returns `{reply, tool_calls:[{name,arguments,result}], model}`. 400 on unknown provider / empty key / empty messages. Upstream LLM failures surface as 502.

## Frontend (`AssistantWidget.tsx`)

Upgrade the existing floating "AI 도우미" widget into a general execution chat:
- Provider selector (Claude/OpenAI/Gemini) + API-key input, persisted in `localStorage` (browser-held, like the esmfold key pattern; never sent to our DB).
- Chat thread with tool-call chips ("🔧 run_model → job abc… ✓") rendered from `tool_calls`.
- Sends `{provider, api_key, messages}` to `chatWithModels()` in `api.ts`.
- Keeps the job-scoped explain mode available; the general mode is the default.

## Scope-out

Streaming; SP4 self-improvement; storing keys server-side; per-tool user confirmation (tools already enforce ownership + a 50MB upload cap).

## Security

Key is request-scoped, never persisted or logged. Tool execution is the SSO user's own workspace. Reuses SP2's hardened `tools.py` (filename sanitize, base64 cap, temp cleanup).
