# One-Message Auto-Chaining Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one chat message launch a linear N-step model chain (rfd3 → proteinMPNN → …); step 1 runs now, later steps run automatically as each predecessor completes.

**Architecture:** A DB-persisted `PendingChain` queue driven by the background `JobMonitor`. A shared `chaining_exec.submit_chained` resolves+submits a job (reused by `run_model`, the new `run_chain` tool, and the monitor). Reuses `app/chaining.py` for artifact/sequence resolution.

**Tech Stack:** FastAPI + SQLAlchemy (SQLite), pytest. Backend-only (no frontend).

**Conventions (verified):**
- Tests run from `portal/backend/`: `.venv/bin/python -m pytest tests/<file> -v`. Use `SessionLocal()` + `monkeypatch` (see `tests/test_mcp_tools.py`).
- `Base.metadata.create_all(bind=engine)` at `app/main.py:17` → new models auto-create (SQLite).
- Completed jobs are `status == "completed"`; active set is `{pending, submitted, running, queued, in_queue, in_progress, processing}`.
- `job_bridge.create_step_job(db, *, user_id, title, pipeline, params, input_files=None, sequence=None)` submits async and returns a Job with status `submitted`.
- `chaining.plan_chain(db, source_job, target_pipeline, source_artifact_ids=None) -> ChainPlan(delivery, artifacts, sequence)`, raises `chaining.ChainError`.

**Import structure (avoids cycles):** `chaining_exec.py` imports only `models`, `runpod.PIPELINES`, `workflow.job_bridge`, `chaining`. `tools.py` and `tasks.py` import FROM `chaining_exec` (never the reverse).

---

## Task 1: Extract `chaining_exec.submit_chained` (+ move shared helpers), delegate `run_model`

**Files:**
- Create: `app/chaining_exec.py`
- Modify: `app/mcp/tools.py` (import helpers from chaining_exec; `run_model` delegates; `job_status`/`cancel_job` use imported helpers)
- Test: `app/../tests/test_chaining_exec.py` (new)

- [ ] **Step 1: Write the failing test**

Create `portal/backend/tests/test_chaining_exec.py`:

```python
import uuid
from pathlib import Path

import pytest

from app import chaining, models
from app import chaining_exec
from app.database import SessionLocal


def _user(db):
    u = models.User(username=f"t_{uuid.uuid4().hex[:8]}", password_hash="x")
    db.add(u); db.commit(); db.refresh(u)
    return u


def _finished_job(db, user, pipeline, artifacts):
    j = models.Job(user_id=user.id, title="src", pipeline=pipeline, status="completed")
    db.add(j); db.commit(); db.refresh(j)
    for name, path, kind in artifacts:
        db.add(models.Artifact(job_id=j.id, file_name=name, file_path=str(path), kind=kind))
    db.commit(); db.refresh(j)
    return j


def _capture(monkeypatch, captured):
    def fake_create(db_, *, user_id, title, pipeline, params, input_files=None, sequence=None):
        captured["input_files"] = [Path(p).name for p in (input_files or [])]
        captured["sequence"] = sequence
        j = models.Job(user_id=user_id, title=title, pipeline=pipeline, status="submitted")
        db_.add(j); db_.commit(); db_.refresh(j)
        return j
    monkeypatch.setattr(chaining_exec.job_bridge, "create_step_job", fake_create)


def test_submit_chained_plain(monkeypatch):
    with SessionLocal() as db:
        u = _user(db)
        captured = {}; _capture(monkeypatch, captured)
        job = chaining_exec.submit_chained(db, u, pipeline="esmfold", params={}, sequence="ACDEF")
        assert job.status == "submitted"
        assert captured["sequence"] == "ACDEF"


def test_submit_chained_files_from_source(monkeypatch, tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        pdb = tmp_path / "backbone.pdb"; pdb.write_text("ATOM\n")
        src = _finished_job(db, u, "rfdiffusion", [("backbone.pdb", pdb, "structure")])
        captured = {}; _capture(monkeypatch, captured)
        job = chaining_exec.submit_chained(db, u, pipeline="diffdock", params={}, from_job_id=src.id)
        assert "backbone.pdb" in captured["input_files"]


def test_submit_chained_sequence_from_source(monkeypatch, tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        fa = tmp_path / "d.fasta"; fa.write_text(">d\nACDEFGHIKL\n")
        src = _finished_job(db, u, "proteinmpnn", [("d.fasta", fa, "generic")])
        captured = {}; _capture(monkeypatch, captured)
        chaining_exec.submit_chained(db, u, pipeline="colabfold", params={}, from_job_id=src.id)
        assert captured["sequence"] == "ACDEFGHIKL"


def test_submit_chained_unknown_pipeline_raises():
    with SessionLocal() as db:
        u = _user(db)
        with pytest.raises(ValueError, match="unknown pipeline"):
            chaining_exec.submit_chained(db, u, pipeline="nope", params={})


def test_submit_chained_unfinished_source_raises(monkeypatch):
    with SessionLocal() as db:
        u = _user(db)
        j = models.Job(user_id=u.id, title="r", pipeline="rfdiffusion", status="running")
        db.add(j); db.commit(); db.refresh(j)
        with pytest.raises(ValueError, match="not finished"):
            chaining_exec.submit_chained(db, u, pipeline="diffdock", params={}, from_job_id=j.id)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /opt/bio_model_portal/portal/backend && .venv/bin/python -m pytest tests/test_chaining_exec.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.chaining_exec'`

- [ ] **Step 3: Create `app/chaining_exec.py`**

```python
"""Shared resolve-and-submit for chained model runs.

Used by the chat tools (run_model / run_chain) and the JobMonitor (dependent
pending-chain steps). Owns the file-staging temp dir and the chaining
resolution; raises ValueError (input problems) or chaining.ChainError.
"""
from __future__ import annotations

import base64
import re
import shutil
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from . import chaining, models
from .runpod import PIPELINES
from .workflow import job_bridge

_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB per file
_ACTIVE_STATUSES = {"pending", "submitted", "running", "queued", "in_queue", "in_progress", "processing"}


def _owned_job(db: Session, user: models.User, job_id: str) -> models.Job | None:
    return db.query(models.Job).filter_by(id=str(job_id or ""), user_id=user.id).first()


def submit_chained(
    db: Session,
    user: models.User,
    *,
    pipeline: str,
    params: dict,
    sequence: str | None = None,
    files: list[dict] | None = None,
    from_job_id: str | None = None,
    source_artifact_ids: list[str] | None = None,
) -> models.Job:
    """Resolve any chained inputs and submit one job. Raises ValueError or
    chaining.ChainError; callers format those for the LLM / mark the chain."""
    pipeline = str(pipeline or "")
    if pipeline not in PIPELINES:
        raise ValueError(f"unknown pipeline '{pipeline}'. valid: {sorted(PIPELINES)}")
    params = params or {}
    input_files: list[Path] = []
    tmp: Path | None = None
    try:
        for item in files or []:
            b64 = item.get("base64")
            if not b64:
                continue
            try:
                data = base64.b64decode(b64)
            except Exception as exc:
                raise ValueError("a file's base64 content is invalid") from exc
            if len(data) > _MAX_UPLOAD_BYTES:
                raise ValueError(f"file exceeds {_MAX_UPLOAD_BYTES} bytes")
            tmp = tmp or Path(tempfile.mkdtemp(prefix="mcp_upload_"))
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(item.get("name") or "input"))
            dest = tmp / (safe or "input")
            dest.write_bytes(data)
            input_files.append(dest)

        if from_job_id or source_artifact_ids:
            if not from_job_id:
                raise ValueError("from_job_id is required when source_artifact_ids is set")
            src = _owned_job(db, user, from_job_id)
            if not src:
                raise ValueError("source job not found")
            if (src.status or "").lower() in _ACTIVE_STATUSES:
                raise ValueError(f"source job {src.id} is not finished (status={src.status})")
            plan = chaining.plan_chain(db, src, pipeline, source_artifact_ids)
            if plan.delivery == "sequence":
                if not (sequence and str(sequence).strip()):
                    sequence = plan.sequence
            else:
                tmp = tmp or Path(tempfile.mkdtemp(prefix="mcp_chain_"))
                for artifact in plan.artifacts:
                    safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(artifact.file_name).name) or "input"
                    dest = tmp / safe
                    dest.write_bytes(Path(artifact.file_path).read_bytes())
                    input_files.append(dest)

        return job_bridge.create_step_job(
            db, user_id=user.id, title=f"mcp {pipeline}", pipeline=pipeline,
            params=params, input_files=input_files or None, sequence=sequence,
        )
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
```

- [ ] **Step 4: Run the new test — expect PASS**

Run: `cd /opt/bio_model_portal/portal/backend && .venv/bin/python -m pytest tests/test_chaining_exec.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Delegate `run_model` to submit_chained and share helpers**

In `app/mcp/tools.py`:
1. Replace the imports block top (lines 1-17) so tools no longer owns the moved helpers. New top:

```python
from __future__ import annotations

from sqlalchemy.orm import Session

from .. import chaining, chaining_exec, models
from ..runpod import PIPELINES
from ..workflow import job_bridge  # noqa: F401  (kept if referenced elsewhere)
from ..chaining_exec import _ACTIVE_STATUSES, _owned_job
```

(Remove now-unused `base64`, `re`, `shutil`, `tempfile`, `Path`, and the local `_MAX_UPLOAD_BYTES` / `_ACTIVE_STATUSES` / `_owned_job` definitions — they now live in `chaining_exec`. Keep `job_bridge`/`PIPELINES` imports only if still referenced; drop with `# noqa` or remove if truly unused. `job_status`/`cancel_job` keep using the imported `_owned_job` and `_ACTIVE_STATUSES`.)

2. Replace the whole `run_model` function body with the thin delegator:

```python
def run_model(db: Session, user: models.User, arguments: dict) -> dict:
    try:
        job = chaining_exec.submit_chained(
            db, user,
            pipeline=str(arguments.get("pipeline") or ""),
            params=arguments.get("parameters") or {},
            sequence=arguments.get("sequence"),
            files=arguments.get("files"),
            from_job_id=arguments.get("from_job_id"),
            source_artifact_ids=arguments.get("source_artifact_ids"),
        )
    except (ValueError, chaining.ChainError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "job_id": job.id, "status": job.status, "endpoint_id": job.endpoint_id}
```

3. Delete the old `_owned_job` definition in tools.py (now imported). Leave `job_status`, `cancel_job`, `list_models`, `_field`, and the `TOOLS` dict intact (they reference `_owned_job`/`_ACTIVE_STATUSES` which are now imported names).

- [ ] **Step 6: Run the full backend suite — no regressions**

Run: `cd /opt/bio_model_portal/portal/backend && .venv/bin/python -m pytest tests/ -q`
Expected: all PASS (existing `test_mcp_tools.py`, `test_jobs_cancel.py`, etc. still green via delegation). Also `python -c "import app.main"` succeeds (no import cycle).

- [ ] **Step 7: Commit**

```bash
cd /opt/bio_model_portal
git add portal/backend/app/chaining_exec.py portal/backend/app/mcp/tools.py portal/backend/tests/test_chaining_exec.py
git commit -m "refactor(chat): extract chaining_exec.submit_chained; run_model delegates"
```

---

## Task 2: `PendingChain` model

**Files:**
- Modify: `app/models.py`
- Test: `tests/test_pending_chain_model.py` (new)

- [ ] **Step 1: Write the failing test**

Create `portal/backend/tests/test_pending_chain_model.py`:

```python
import uuid

from app import models
from app.database import SessionLocal


def test_pending_chain_roundtrips_steps_json():
    with SessionLocal() as db:
        u = models.User(username=f"t_{uuid.uuid4().hex[:8]}", password_hash="x")
        db.add(u); db.commit(); db.refresh(u)
        pc = models.PendingChain(
            user_id=u.id, source_job_id="job-1",
            steps=[{"pipeline": "proteinmpnn", "parameters": {}}], status="pending")
        db.add(pc); db.commit(); db.refresh(pc)
        assert pc.id and pc.status == "pending"
        assert pc.steps[0]["pipeline"] == "proteinmpnn"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /opt/bio_model_portal/portal/backend && .venv/bin/python -m pytest tests/test_pending_chain_model.py -v`
Expected: FAIL — `AttributeError: module 'app.models' has no attribute 'PendingChain'`

- [ ] **Step 3: Add the model**

Append to `app/models.py` (uses the already-imported `Base`, `Mapped`, `mapped_column`, `String`, `Integer`, `ForeignKey`, `DateTime`, `JSON`, `datetime`, `uuid4`, `Optional`):

```python
class PendingChain(Base):
    __tablename__ = "pending_chains"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    # The job this chain is currently waiting on to finish.
    source_job_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    # Ordered remaining steps: [{"pipeline", "parameters", "source_artifact_ids"?}].
    steps: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|processing|done|cancelled|failed
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
```

- [ ] **Step 4: Run test — expect PASS**

Run: `cd /opt/bio_model_portal/portal/backend && .venv/bin/python -m pytest tests/test_pending_chain_model.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /opt/bio_model_portal
git add portal/backend/app/models.py portal/backend/tests/test_pending_chain_model.py
git commit -m "feat(models): PendingChain table for queued dependent steps"
```

---

## Task 3: `run_chain` tool + loop.py attachment injection + SYSTEM_PROMPT

**Files:**
- Modify: `app/mcp/tools.py` (`run_chain` + `TOOLS` entry)
- Modify: `app/chat/loop.py` (attachment injection + SYSTEM_PROMPT)
- Test: `tests/test_mcp_tools.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `portal/backend/tests/test_mcp_tools.py` (reuses `_user`, `SessionLocal`, `models`, `tools`, `Path`, `_capture_create` already present from earlier tasks):

```python
def test_run_chain_submits_first_and_queues_rest(monkeypatch):
    with SessionLocal() as db:
        u = _user(db)
        captured = {}; _capture_create(monkeypatch, captured)
        r = tools.run_chain(db, u, {
            "steps": [{"pipeline": "rfdiffusion", "parameters": {}},
                      {"pipeline": "proteinmpnn", "parameters": {}}],
            "sequence": "ACDEF",
        })
        assert r["ok"] is True and r["first_job_id"]
        assert r["queued"] == ["proteinmpnn"]
        chains = db.query(models.PendingChain).filter_by(source_job_id=r["first_job_id"]).all()
        assert len(chains) == 1
        assert chains[0].steps == [{"pipeline": "proteinmpnn", "parameters": {}}]


def test_run_chain_single_step_creates_no_pending(monkeypatch):
    with SessionLocal() as db:
        u = _user(db)
        captured = {}; _capture_create(monkeypatch, captured)
        r = tools.run_chain(db, u, {"steps": [{"pipeline": "esmfold"}], "sequence": "ACDEF"})
        assert r["ok"] is True and r["queued"] == []
        assert db.query(models.PendingChain).filter_by(source_job_id=r["first_job_id"]).count() == 0


def test_run_chain_unknown_pipeline_errors(monkeypatch):
    with SessionLocal() as db:
        u = _user(db)
        r = tools.run_chain(db, u, {"steps": [{"pipeline": "nope"}]})
        assert r["ok"] is False and "unknown pipeline" in r["error"].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /opt/bio_model_portal/portal/backend && .venv/bin/python -m pytest tests/test_mcp_tools.py -k run_chain -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'run_chain'`

- [ ] **Step 3: Add `run_chain` to `app/mcp/tools.py`**

Add this function (near `run_model`):

```python
def run_chain(db: Session, user: models.User, arguments: dict) -> dict:
    steps = arguments.get("steps") or []
    if not steps:
        return {"ok": False, "error": "steps is required (at least one step)"}
    for s in steps:
        p = str((s or {}).get("pipeline") or "")
        if p not in PIPELINES:
            return {"ok": False, "error": f"unknown pipeline '{p}'. valid: {sorted(PIPELINES)}"}
    first = steps[0]
    try:
        job = chaining_exec.submit_chained(
            db, user,
            pipeline=str(first.get("pipeline")),
            params=first.get("parameters") or {},
            sequence=arguments.get("sequence"),
            files=arguments.get("files"),
            from_job_id=first.get("from_job_id"),
            source_artifact_ids=first.get("source_artifact_ids"),
        )
    except (ValueError, chaining.ChainError) as exc:
        return {"ok": False, "error": str(exc)}
    rest = steps[1:]
    if rest:
        db.add(models.PendingChain(
            user_id=user.id, source_job_id=job.id, steps=rest, status="pending"))
        db.commit()
    return {"ok": True, "first_job_id": job.id, "first_status": job.status,
            "queued": [str(s.get("pipeline")) for s in rest]}
```

Add the `run_chain` entry to the `TOOLS` dict:

```python
    "run_chain": (run_chain,
        "Run an ordered multi-step chain in one call (e.g. rfdiffusion then proteinmpnn "
        "then colabfold then diffdock). Step 1 runs immediately from the user's attached "
        "input/sequence; each later step runs AUTOMATICALLY when its predecessor finishes, "
        "feeding the predecessor's output in. Use this when the user asks to do several "
        "models in sequence in one message. A queued (non-first) step that needs an extra "
        "uploaded file (e.g. a DiffDock SDF ligand) is not supported — use a SMILES ligand "
        "in that step's parameters, or run it separately. After calling, tell the user step 1 "
        "started and which steps are queued.", {
        "type": "object",
        "properties": {
            "steps": {"type": "array", "items": {"type": "object", "properties": {
                "pipeline": {"type": "string"},
                "parameters": {"type": "object"},
            }, "required": ["pipeline"]}},
            "sequence": {"type": "string"},
        },
        "required": ["steps"],
    }),
```

- [ ] **Step 4: Inject attachments into `run_chain` step 1 (loop.py)**

In `app/chat/loop.py`, find the attachment injection (currently only for `run_model`):

```python
            if call["name"] == "run_model" and attachments:
                args = {**args, "files": attachments}
```

Replace with:

```python
            if call["name"] in ("run_model", "run_chain") and attachments:
                # Inject the real uploads (run_chain applies them to step 1). The LLM
                # never has to echo base64; it may hallucinate a name-only files arg.
                args = {**args, "files": attachments}
```

- [ ] **Step 5: Update SYSTEM_PROMPT (loop.py)**

In `app/chat/loop.py`, replace the SYSTEM_PROMPT string. Add the chaining-in-one-call guidance; keep the existing `from_job_id` single-step guidance. New value:

```python
SYSTEM_PROMPT = (
    "You are the Bio Model Portal assistant. You help users run and interpret "
    "the portal's protein models (folding, docking, design, phage analysis). "
    "You can call tools to list models, run a model, run an ordered multi-step "
    "chain, check job status, fetch a job's results, and cancel a job. Call "
    "list_models first when you are unsure of a model's key or parameters. "
    "For a SINGLE step that reuses one finished job's output, call run_model "
    "with from_job_id. For a MULTI-STEP request in one message (e.g. 'make a "
    "backbone with rfdiffusion then design a sequence with proteinmpnn'), call "
    "run_chain with the ordered steps: step 1 runs now from the attached "
    "input, and each later step runs automatically when its predecessor "
    "finishes. Tell the user step 1 started and which steps are queued. A "
    "queued step that needs an extra uploaded file (e.g. a DiffDock SDF ligand) "
    "is not supported — use a SMILES ligand parameter or run it separately. "
    "DiffDock always needs a ligand (SMILES/SDF). If two models cannot connect, "
    "explain the compatible options. After running or fetching results, explain "
    "them clearly and concisely in the user's language. Never fabricate job IDs "
    "or results — always use the tools."
)
```

- [ ] **Step 6: Run tests — expect PASS**

Run: `cd /opt/bio_model_portal/portal/backend && .venv/bin/python -m pytest tests/test_mcp_tools.py tests/test_chat.py -v`
Expected: PASS (new run_chain tests + existing).

- [ ] **Step 7: Commit**

```bash
cd /opt/bio_model_portal
git add portal/backend/app/mcp/tools.py portal/backend/app/chat/loop.py portal/backend/tests/test_mcp_tools.py
git commit -m "feat(chat): run_chain tool queues a multi-step chain from one message"
```

---

## Task 4: JobMonitor `_advance_pending_chains` hook

**Files:**
- Modify: `app/tasks.py`
- Test: `tests/test_pending_chain_monitor.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `portal/backend/tests/test_pending_chain_monitor.py`:

```python
import uuid
from pathlib import Path

from app import models
from app.database import SessionLocal
from app.tasks import monitor


def _user(db):
    u = models.User(username=f"t_{uuid.uuid4().hex[:8]}", password_hash="x")
    db.add(u); db.commit(); db.refresh(u)
    return u


def _completed_with_structure(db, user, tmp_path, pipeline="rfdiffusion"):
    j = models.Job(user_id=user.id, title="src", pipeline=pipeline, status="completed")
    db.add(j); db.commit(); db.refresh(j)
    pdb = tmp_path / "backbone.pdb"; pdb.write_text("ATOM\n")
    db.add(models.Artifact(job_id=j.id, file_name="backbone.pdb", file_path=str(pdb), kind="structure"))
    db.commit(); db.refresh(j)
    return j


def _capture(monkeypatch, captured):
    from app import chaining_exec
    def fake_create(db_, *, user_id, title, pipeline, params, input_files=None, sequence=None):
        captured.setdefault("jobs", []).append(
            {"pipeline": pipeline, "files": [Path(p).name for p in (input_files or [])]})
        j = models.Job(user_id=user_id, title=title, pipeline=pipeline, status="submitted")
        db_.add(j); db_.commit(); db_.refresh(j)
        return j
    monkeypatch.setattr(chaining_exec.job_bridge, "create_step_job", fake_create)


def test_advance_submits_next_step_on_completion(monkeypatch, tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        src = _completed_with_structure(db, u, tmp_path)
        db.add(models.PendingChain(user_id=u.id, source_job_id=src.id,
                                   steps=[{"pipeline": "proteinmpnn", "parameters": {}}], status="pending"))
        db.commit()
        captured = {}; _capture(monkeypatch, captured)
        monitor._advance_pending_chains(db, src, ok=True); db.commit()
        assert captured["jobs"][0]["pipeline"] == "proteinmpnn"
        assert "backbone.pdb" in captured["jobs"][0]["files"]
        pc = db.query(models.PendingChain).filter_by(source_job_id=src.id).one()
        assert pc.status == "done"


def test_advance_repoints_multi_step_chain(monkeypatch, tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        src = _completed_with_structure(db, u, tmp_path)
        db.add(models.PendingChain(user_id=u.id, source_job_id=src.id, status="pending",
            steps=[{"pipeline": "proteinmpnn", "parameters": {}},
                   {"pipeline": "colabfold", "parameters": {}}]))
        db.commit()
        captured = {}; _capture(monkeypatch, captured)
        monitor._advance_pending_chains(db, src, ok=True); db.commit()
        pc = db.query(models.PendingChain).filter_by(user_id=u.id).one()
        assert pc.status == "pending"
        assert pc.steps == [{"pipeline": "colabfold", "parameters": {}}]
        assert pc.source_job_id != src.id  # re-pointed to the new proteinmpnn job


def test_advance_cancels_on_failure(monkeypatch, tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        src = _completed_with_structure(db, u, tmp_path)
        src.status = "failed"; db.commit()
        db.add(models.PendingChain(user_id=u.id, source_job_id=src.id,
                                   steps=[{"pipeline": "proteinmpnn"}], status="pending"))
        db.commit()
        monitor._advance_pending_chains(db, src, ok=False); db.commit()
        assert db.query(models.PendingChain).filter_by(source_job_id=src.id).one().status == "cancelled"


def test_advance_is_idempotent(monkeypatch, tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        src = _completed_with_structure(db, u, tmp_path)
        db.add(models.PendingChain(user_id=u.id, source_job_id=src.id,
                                   steps=[{"pipeline": "proteinmpnn"}], status="pending"))
        db.commit()
        captured = {}; _capture(monkeypatch, captured)
        monitor._advance_pending_chains(db, src, ok=True); db.commit()
        monitor._advance_pending_chains(db, src, ok=True); db.commit()  # second call: nothing pending
        assert len(captured.get("jobs", [])) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /opt/bio_model_portal/portal/backend && .venv/bin/python -m pytest tests/test_pending_chain_monitor.py -v`
Expected: FAIL — `AttributeError: 'JobMonitor' object has no attribute '_advance_pending_chains'`

- [ ] **Step 3: Add the hook method + call sites in `app/tasks.py`**

Add `import` for chaining_exec near the top imports:

```python
from . import chaining_exec
```

Add this method to the `JobMonitor` class (e.g. after `_update_job`):

```python
    def _advance_pending_chains(self, db: Session, job: models.Job, ok: bool) -> None:
        chains = db.query(models.PendingChain).filter_by(
            source_job_id=job.id, status="pending").all()
        for chain in chains:
            if not ok:
                chain.status = "cancelled"
                chain.error = f"source job {job.id} did not complete"
                continue
            chain.status = "processing"  # guard against a re-poll double-firing
            owner = db.query(models.User).get(chain.user_id)
            steps = list(chain.steps or [])
            step, rest = steps[0], steps[1:]
            try:
                new_job = chaining_exec.submit_chained(
                    db, owner,
                    pipeline=step["pipeline"],
                    params=step.get("parameters") or {},
                    from_job_id=job.id,
                    source_artifact_ids=step.get("source_artifact_ids"),
                )
            except Exception as exc:  # noqa: BLE001 (ChainError / ValueError)
                chain.status = "failed"
                chain.error = str(exc)
                continue
            if rest:
                chain.source_job_id = new_job.id
                chain.steps = rest
                chain.status = "pending"
            else:
                chain.status = "done"
```

Wire the call sites inside `_update_job`:
- Right after `self._persist_output(db, job, output)` (the COMPLETED branch), add:
  ```python
            self._advance_pending_chains(db, job, ok=True)
  ```
- In the worker-error early return (where `job.status = "failed"` is set after `if (output_status := ...) in {"error", "failed"}`), before `return`, add:
  ```python
            self._advance_pending_chains(db, job, ok=False)
  ```
- In the terminal-failure `elif status_upper in {"FAILED", "TIMED_OUT", "CANCELLED", "COMPLETED_WITH_ERRORS"}:` branch, add:
  ```python
            self._advance_pending_chains(db, job, ok=False)
  ```

- [ ] **Step 4: Run tests — expect PASS**

Run: `cd /opt/bio_model_portal/portal/backend && .venv/bin/python -m pytest tests/test_pending_chain_monitor.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
cd /opt/bio_model_portal
git add portal/backend/app/tasks.py portal/backend/tests/test_pending_chain_monitor.py
git commit -m "feat(monitor): auto-advance pending chains when a source job finishes"
```

---

## Task 5: Full-suite + offline end-to-end verification

- [ ] **Step 1: Full backend suite**

Run: `cd /opt/bio_model_portal/portal/backend && .venv/bin/python -m pytest tests/ -q`
Expected: all PASS. Also `.venv/bin/python -c "import app.main"` (no import cycle).

- [ ] **Step 2: Offline end-to-end smoke (drive run_chain → simulate completion → hook fires)**

Confirm the whole path with a throwaway script: `run_chain` submits step 1 and queues proteinmpnn; then mark the rfd3 job `completed` with a structure artifact and call `monitor._advance_pending_chains(db, job, ok=True)`; assert a proteinmpnn job was created with `backbone.pdb` as input and the PendingChain is `done`. (Mock `chaining_exec.job_bridge.create_step_job`.) This exercises tool → queue → monitor without external services.

- [ ] **Step 3: Final commit / summary**

```bash
cd /opt/bio_model_portal && git log --oneline feat/one-message-auto-chaining -6
```

---

## Self-Review Notes

- **Spec coverage:** submit_chained refactor (Task 1) ↔ §1; PendingChain (Task 2) ↔ §2; run_chain + loop injection + prompt (Task 3) ↔ §3,§5; monitor hook incl. completed/failed/idempotent (Task 4) ↔ §4 + Error Handling; verification (Task 5) ↔ §Testing. Non-goals (SDF-file downstream, branching DAG, Monitor "queued" row) intentionally excluded.
- **Signature consistency:** `chaining_exec.submit_chained(db, user, *, pipeline, params, sequence=None, files=None, from_job_id=None, source_artifact_ids=None) -> Job` is called identically by `run_model`, `run_chain`, and `_advance_pending_chains`. `PendingChain.steps` shape `[{"pipeline","parameters","source_artifact_ids"?}]` is written by run_chain and read by the monitor identically. `_advance_pending_chains(db, job, ok)` name/signature matches tests and call sites.
- **Import cycle:** `chaining_exec` imports only models/runpod/job_bridge/chaining; `tools` and `tasks` import from `chaining_exec`. Verified acyclic.
- **Ordering:** the completed-branch hook is placed AFTER `_persist_output` so structure artifacts are indexed before `plan_chain` runs.
