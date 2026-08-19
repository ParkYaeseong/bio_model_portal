# Self-Improvement v1 (SP4) Design

**Goal:** Capture chatbot interactions + model-run outcomes globally (anonymized), analyze them on a schedule to produce improvement artifacts (better default parameters, failure-pattern warnings, successful recipes), and feed the *approved* artifacts back into the chatbot — all server-side, independent of any user terminal/browser. Global-first; per-user personalization and autonomous self-rewrite are explicitly out of scope for v1.

**North star (later):** an AI that, given a goal + input, auto-selects the model/workflow/params from accumulated data. This design builds the data foundation and the first data-driven recommendation loop toward that.

**Architecture:** New `app/selfimprove/` package in the FastAPI backend. Two new DB tables (`interactions`, `improvement_artifacts`). The existing `/api/chat` loop records each turn; a periodic scheduler links job outcomes and recomputes artifacts nightly; the chat system prompt is enriched with the currently-active (admin-approved) artifact. Everything runs under the existing `bmp-backend` systemd service + an in-process scheduler thread — no terminal dependency.

**Tech stack:** FastAPI, SQLAlchemy 2.0 (existing), a lightweight background scheduler (a daemon thread with a sleep loop — no new heavy dependency like Celery/APScheduler unless already present), pytest.

---

## Data model (`app/models.py`)

### `interactions` table
One row per assistant turn in `/api/chat`.

| column | type | notes |
|---|---|---|
| `id` | str (uuid) PK | |
| `user_id` | int FK users | for per-user counts only; never surfaced cross-user |
| `created_at` | datetime | |
| `provider` | str | anthropic/openai/gemini |
| `model` | str | e.g. claude-opus-4-8 |
| `user_message_len` | int | length only — **raw user text is NOT stored** |
| `reply_len` | int | |
| `tool_calls` | JSON | list of `{name, arguments_sanitized, ok}` — `arguments_sanitized` strips `files` base64 and any obviously-raw sequence/PDB payload (see Privacy) |
| `job_ids` | JSON | job ids created by run_model calls this turn (for outcome linking) |
| `error` | str \| null | client-visible error if the turn failed |

### `improvement_artifacts` table
Versioned output of the analysis job.

| column | type | notes |
|---|---|---|
| `id` | str (uuid) PK | |
| `created_at` | datetime | |
| `status` | str | `proposed` \| `active` \| `rejected` |
| `summary` | str | human-readable one-liner |
| `payload` | JSON | `{recommended_defaults: {pipeline: {param: value}}, warnings: [{pipeline, condition, message}], recipes: [{goal, steps}]}` |
| `stats` | JSON | counts the artifact was derived from (n_jobs, n_success, per-pipeline) |

At most one artifact is `active` at a time. The chat loop reads the active one.

---

## Components

### 1. Capture (`app/selfimprove/capture.py`)
`record_interaction(db, user, provider, model, history, result)` — called from `routers/chat.py` after `run_chat` returns (best-effort; wrapped so a capture failure never breaks the chat response). Sanitizes tool-call arguments (drop `files`, truncate any string > 512 chars to a length marker) and extracts `job_ids` from run_model results.

### 2. Outcome reading (`app/selfimprove/outcomes.py`)
Outcomes are **read live at analysis time** — no separate linking/storage step. `job_outcome(db, job_id) -> {status, success, metrics}` best-effort joins `interactions.job_ids` → the `jobs` row and parses quality metrics (`job_metrics(job)` reads `output.json` for pLDDT/ipTM where present; success = terminal `completed`). Read-only over existing job rows, so there is nothing to keep in sync.

### 3. Analysis job (`app/selfimprove/analyze.py`)
`compute_artifact(db) -> dict` — pure function over all interactions + linked jobs (global, anonymized aggregate):
- **recommended_defaults**: for each pipeline, the most common parameter set among *successful* jobs (min support threshold, else omit).
- **warnings**: parameter conditions with a high failure rate (e.g. `proteinmpnn` runs with no input file → failed) above a threshold.
- **recipes**: frequent successful multi-tool sequences within a turn/conversation.
Returns the artifact `payload` + `stats`. Writes a new `improvement_artifacts` row with `status="proposed"`.

### 4. Scheduler (`app/selfimprove/scheduler.py`)
A daemon thread started on FastAPI startup: every `SELFIMPROVE_INTERVAL_S` (default 24h; configurable, short in tests) calls `compute_artifact(db)` (which reads job outcomes live). Guarded by a `SELFIMPROVE_ENABLED` setting (default True in prod, False in tests to keep the suite deterministic).

### 5. Feedback + approval gate
- `active_artifact(db)` → the `active` artifact (or None).
- `routers/chat.py` / `loop.py`: `SYSTEM_PROMPT` is augmented with a compact rendering of the active artifact's `recommended_defaults` + `warnings` ("Learned from prior runs: …"). Low-risk (surfacing stats/warnings) is always on when an artifact is active.
- **Approval gate**: a new artifact is `proposed`; it does not affect behavior until promoted to `active`. Promotion is a deliberate admin action:
  - `POST /api/selfimprove/artifacts/{id}/activate` and `/reject` — admin-gated by a `SELFIMPROVE_ADMIN_USERS` allowlist (comma-separated X-KBF-User subs; empty allowlist → endpoints return 403 for everyone, so promotion is impossible until an admin is configured).
  - `GET /api/selfimprove/artifacts` — list proposed/active for review.
- `SELFIMPROVE_AUTOACTIVATE` setting (default False): when True, a freshly computed artifact is auto-activated (for orgs that want the loop hands-off). Default keeps the human gate.

### Admin review UI (minimal, deferred-optional)
v1 ships the API only; a small admin page can follow. (Out of scope to keep v1 focused.)

---

## Privacy

- **No raw research data stored**: user message text is reduced to a length; tool-call `files` base64 is dropped; any tool argument string > 512 chars becomes a length marker. Sequences/PDBs never enter `interactions`.
- **Global learning is aggregate-only**: artifacts contain parameter/outcome statistics, never another user's raw inputs/results.
- **No cross-user raw surfacing**: the chat loop injects only aggregate defaults/warnings.

---

## Error handling

- Capture is best-effort: any exception in `record_interaction` is caught and logged; the chat response is unaffected.
- The scheduler catches per-cycle exceptions and continues; a failed cycle never crashes the app.
- Analysis with insufficient data emits an artifact with empty `recommended_defaults`/`warnings` (no fabrication) and low `stats` counts.

## Testing

- `analyze.compute_artifact` is a pure function → unit tests with seeded interactions+jobs assert recommended defaults / warnings / recipes.
- Capture sanitization: base64 and long strings are stripped; job_ids extracted.
- Approval gate: proposed artifact does not affect `active_artifact`; activate promotes exactly one; endpoints are admin-gated.
- Scheduler disabled under tests (`SELFIMPROVE_ENABLED=False`) so the suite stays deterministic; the cycle function is tested by direct call.

## Scope-out (later)

Autonomous self-rewrite (always human-approved in v1), per-user personalization (global-first), learned ML selector, MLflow integration, admin UI.

## Phasing

1. **Capture + outcome linking** (tables, capture hook, `job_metrics`) — independently valuable (populates the dataset).
2. **Analysis + artifacts** (`compute_artifact`, scheduler).
3. **Feedback + approval gate** (system-prompt injection, admin endpoints).
