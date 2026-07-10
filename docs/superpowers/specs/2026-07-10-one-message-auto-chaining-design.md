# One-Message Auto-Chaining (pending-chain queue)

**Date:** 2026-07-10
**Status:** Design approved, pending implementation plan
**Builds on:** 2026-07-10-chatbot-cross-job-chaining-design.md (from_job_id chaining, `app/chaining.py`)

## Problem

"이 서열로 rfd3 백본 만들고 proteinMPNN으로 서열까지" in one chat message cannot
complete today: `run_model` submits the rfd3 job asynchronously (status
`submitted`), and chaining proteinMPNN off it immediately fails with
`"source job is not finished"` (auto-wait was deferred). The assistant either
stops after step 1 or loops to the iteration cap and returns the
"도구 호출이 한도에 도달했습니다" message — which reads as a cut-off.

The chat turn is **request-scoped and synchronous**; it cannot block for the
minutes an rfd3 job takes without re-introducing the proxy-timeout cut-off.
So dependent steps must run **asynchronously** after their predecessor finishes.

## Goal

Let a single chat message launch a linear N-step chain (e.g.
rfd3 → proteinMPNN → ColabFold → DiffDock). Step 1 runs immediately; each later
step runs automatically when its predecessor completes, chaining the
predecessor's output via the existing `chaining.plan_chain`.

## Approach (chosen)

Lightweight **pending-chain queue** persisted in the DB, driven by the existing
background `JobMonitor`. No blocking in the chat turn; no new orchestration
engine. Reuses `app/chaining.py` for artifact/sequence resolution.

## Non-Goals (YAGNI)

- Downstream steps that need an **extra file upload** (e.g. a DiffDock **SDF
  file** ligand). SMILES ligands work (a `parameters` value); SDF-file ligands
  on a *queued* step are out of MVP — the assistant collects SMILES or the user
  runs that step manually. This limit is `log()`-ged, never silent.
- Branching / non-linear DAGs (that is the workflow orchestrator's job).
- A dedicated "queued step" row in the Monitor UI. Queued steps become normal
  jobs (visible in Monitor) as each fires; the chat reply states what is queued.
- Crash-recovery of a chain stuck mid-submit (acceptable for MVP).

## Architecture

### 1. `app/chaining_exec.py` (new) — shared resolve-and-submit

Extract the core of today's `run_model` into one reusable function used by both
the chat tools and the monitor (DRY):

```python
def submit_chained(
    db, user, *, pipeline, params, sequence=None,
    files=None,                 # [{name, base64}] chat attachments (step 1)
    from_job_id=None, source_artifact_ids=None,
) -> models.Job:
    """Resolve any chained inputs and submit one job. Raises ValueError
    (unknown pipeline / bad base64 / oversize) or chaining.ChainError."""
```

Body = current `run_model` body: validate pipeline; one temp dir; decode `files`
base64 → temp files; if `from_job_id`/`source_artifact_ids` → `_owned_job` +
finished check + `chaining.plan_chain` → inject structure files or set sequence;
`job_bridge.create_step_job(...)`; `finally: rmtree(tmp)`. Returns the Job.

`_ACTIVE_STATUSES` moves here (or stays in tools and is imported). The
"not finished" active-status check lives inside `submit_chained`.

### 2. `PendingChain` model (`app/models.py`)

```python
class PendingChain(Base):
    __tablename__ = "pending_chains"
    id: str (uuid, pk)
    user_id: int (FK users.id)
    source_job_id: str            # the job this chain is waiting on
    steps: JSON                   # ordered [{pipeline, parameters, source_artifact_ids?}]
    status: str                   # pending | processing | done | cancelled | failed
    error: str | None
    created_at: datetime
```

Auto-created by `Base.metadata.create_all` (main.py:17), SQLite.

### 3. `run_chain` chat tool (`app/mcp/tools.py`)

Schema: `run_chain(steps: [{pipeline, parameters?}], sequence?, files?)`,
`required: ["steps"]` (≥1 step). `files` is injected by `loop.py` from the
user's chat attachments (see §5) and applies to step 1.

Behavior:
1. Validate every `steps[i].pipeline` is a known PIPELINE; error if not.
2. Submit **step 1** now: `submit_chained(db, user, pipeline=steps[0].pipeline,
   params=steps[0].parameters, sequence=sequence, files=files)` → `first_job`.
3. If `len(steps) > 1`: create `PendingChain(user_id, source_job_id=first_job.id,
   steps=steps[1:], status="pending")`.
4. Return `{"ok": True, "first_job_id": ..., "first_status": ...,
   "queued": [s["pipeline"] for s in steps[1:]]}`. On `ValueError`/`ChainError`
   → `{"ok": False, "error": ...}`.

`run_model` becomes a thin wrapper over `submit_chained` (keeps `from_job_id` /
`source_artifact_ids` for single-step chaining; existing tests must still pass).

### 4. `JobMonitor` hook (`app/tasks.py`)

In `_update_job`, after a job reaches a terminal state:
- **completed** (right after `self._persist_output(db, job, output)`, so the
  structure artifacts are already indexed): `self._advance_pending_chains(db, job, ok=True)`.
- **failed** paths (lines ~106 and ~110): `self._advance_pending_chains(db, job, ok=False)`.

```python
def _advance_pending_chains(self, db, job, ok):
    chains = db.query(models.PendingChain).filter_by(
        source_job_id=job.id, status="pending").all()
    for chain in chains:
        if not ok:
            chain.status = "cancelled"
            chain.error = f"source job {job.id} did not complete"
            continue
        chain.status = "processing"          # idempotency guard
        owner = db.query(models.User).get(chain.user_id)
        step, rest = chain.steps[0], chain.steps[1:]
        try:
            new_job = chaining_exec.submit_chained(
                db, owner, pipeline=step["pipeline"],
                params=step.get("parameters") or {},
                from_job_id=job.id,
                source_artifact_ids=step.get("source_artifact_ids"))
        except Exception as exc:             # ChainError / ValueError
            chain.status = "failed"
            chain.error = str(exc)
            continue
        if rest:
            chain.source_job_id = new_job.id
            chain.steps = rest
            chain.status = "pending"         # advance the cursor
        else:
            chain.status = "done"
```

The newly-submitted job has status `submitted` → picked up by the next
`_poll_once` cycle, so the chain progresses one step per completion. The
completed source job is no longer in `ACTIVE_JOB_STATUSES`, so the hook fires
once per job (plus the status guard). `db.commit()` happens in `_poll_once`.

### 5. `loop.py` — attachments for `run_chain` step 1 + prompt

- Attachment injection currently special-cases `run_model`
  (`args = {**args, "files": attachments}`). Generalize to also inject into
  `run_chain` (its `files` → step 1).
- SYSTEM_PROMPT: add — "For a multi-step request (do A, then B with A's output,
  then C…), call `run_chain` with the ordered steps. Step 1 runs immediately;
  each later step runs automatically when its predecessor finishes. Tell the
  user step 1 started and the rest are queued. A queued step that needs an
  extra uploaded file (e.g. a DiffDock SDF ligand) is not supported — use a
  SMILES ligand or run that step after."

## Error Handling

- Unknown pipeline / bad base64 / oversize → `ValueError` → `{"ok": False, ...}`.
- Chain not resolvable at fire time (e.g. no structure artifact) →
  `ChainError` caught in the monitor → `PendingChain.status="failed"` + `error`.
- Source job failed/cancelled → dependent chains `cancelled`.
- Double poll → `status="processing"` guard prevents re-submit.

## Testing (`portal/backend/tests/`)

- `test_chaining_exec.py`: `submit_chained` — plain submit; files-delivery from a
  finished source; sequence-delivery; unfinished source raises; unknown pipeline
  raises. (Mocks `job_bridge.create_step_job` like `test_mcp_tools.py`.)
- `test_mcp_tools.py` (extend): `run_chain` submits step 1 and creates a
  `PendingChain` with `steps[1:]`; single-step `run_chain` creates no chain;
  existing `run_model` tests still pass (delegation).
- `test_pending_chain_monitor.py`: given a completed source job with a structure
  artifact + a `PendingChain`, `_advance_pending_chains(ok=True)` submits the
  next step (chained input present) and, for a 3-step chain, re-points
  `source_job_id`/`steps`; `ok=False` cancels; a `ChainError` at fire time →
  `failed`; second call is idempotent (guard).
- Offline integration: simulate completion → hook → next job created with the
  chained structure as input.

## Files Touched

- `app/chaining_exec.py` (new), `app/models.py` (PendingChain),
  `app/mcp/tools.py` (run_chain + run_model delegates to submit_chained),
  `app/chat/loop.py` (attachment injection + SYSTEM_PROMPT),
  `app/tasks.py` (_advance_pending_chains hook), tests.

No frontend change (queued steps surface as jobs in Monitor as they run). No
change to `chaining.py`, `job_bridge`, `runpod`, or the workflow orchestrator.
