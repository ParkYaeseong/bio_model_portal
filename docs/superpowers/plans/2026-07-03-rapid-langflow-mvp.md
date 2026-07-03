# RAPID Langflow Workflow MVP (SP1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a portal user instantiate and run the fixed RAPID protein-design workflow from the UI, with per-step params, tracked async multi-step execution, and a candidate report.

**Architecture:** Portal-orchestrated. A `WorkflowMonitor` daemon thread advances a `WorkflowRun` through DAG steps; GPU steps create ordinary portal `Job`s (reusing gateway submission + `JobMonitor` + artifact storage); portal-side steps (conservation, mock SoluProt, report) run inline. Langflow is deferred to Phase 2; the DAG is a versioned JSON template.

**Tech Stack:** FastAPI + SQLAlchemy (SQLite) backend, Next.js 14 (App Router) frontend, existing RunPod-compat gateway.

Spec: `docs/superpowers/specs/2026-07-03-rapid-langflow-mvp-design.md`

All backend paths are relative to `portal/backend/`; run backend commands from there with `.venv/bin/python`. All frontend paths relative to `portal/frontend/`.

---

## File Structure

**Create (backend):**
- `app/workflow/__init__.py` — package marker.
- `app/workflow/templates/rapid_v1.json` — the RAPID DAG.
- `app/workflow/template_loader.py` — load/validate DAG templates.
- `app/workflow/conservation.py` — MSA → tiered fixed-position mask (pure).
- `app/workflow/soluprot_mock.py` — deterministic mock solubility scorer (pure).
- `app/workflow/job_bridge.py` — create a portal `Job` for a worker step and submit it.
- `app/workflow/orchestrator.py` — build step inputs, run portal-side steps, advance the DAG.
- `app/workflow/monitor.py` — `WorkflowMonitor` daemon thread.
- `app/routers/workflows.py` — `/api/workflows` REST API.
- `tests/test_conservation.py`, `tests/test_soluprot_mock.py`, `tests/test_template_loader.py`, `tests/test_orchestrator.py`, `tests/test_workflows_api.py`.

**Modify (backend):**
- `app/models.py` — add `Workflow`, `WorkflowRun`, `WorkflowRunStep`.
- `app/main.py` — register `workflows` router; start/stop `WorkflowMonitor`.
- `app/routers/__init__.py` — export `workflows`.

**Create (frontend):**
- `src/app/workflows/page.tsx` — list.
- `src/app/workflows/new/page.tsx` — create/instantiate + params + run.
- `src/app/workflows/runs/[runId]/page.tsx` — run detail + report.

**Modify (frontend):**
- `src/lib/api.ts` — workflow types + fetchers.
- `src/app/page.tsx` — add a `워크플로우` nav link to `/workflows`.

**Docs:** `README.md` — workflow section.

---

## Task 1: Database models

**Files:**
- Modify: `app/models.py`
- Test: `tests/test_models_workflow.py`

- [ ] **Step 1: Write the failing test**

Create `portal/backend/tests/test_models_workflow.py`:

```python
from app import models
from app.database import Base, engine, SessionLocal


def test_workflow_models_create_and_cascade():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        user = models.User(username="wf_tester", password_hash="x")
        db.add(user)
        db.commit()
        wf = models.Workflow(name="RAPID", template_key="rapid_v1", owner_id=user.id, dag={"nodes": []})
        db.add(wf)
        db.commit()
        run = models.WorkflowRun(workflow_id=wf.id, owner_id=user.id, status="queued")
        db.add(run)
        db.commit()
        step = models.WorkflowRunStep(run_id=run.id, order=0, step_name="MSA Search", worker_name="gateway.mmseqs", status="queued")
        db.add(step)
        db.commit()
        assert step.id and run.id and wf.id
        db.delete(run)
        db.commit()
        assert db.query(models.WorkflowRunStep).filter_by(id=step.id).first() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd portal/backend && .venv/bin/python -m pytest tests/test_models_workflow.py -q`
Expected: FAIL with `AttributeError: module 'app.models' has no attribute 'Workflow'`.

- [ ] **Step 3: Add the models**

Append to `app/models.py` (after `Artifact`):

```python
class Workflow(Base):
    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    template_key: Mapped[str] = mapped_column(String(64), nullable=False)
    dag: Mapped[dict | None] = mapped_column(JSON, default=dict)
    langflow_flow_id: Mapped[Optional[str]] = mapped_column(String(80))  # Phase 2
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    runs: Mapped[list[WorkflowRun]] = relationship(
        "WorkflowRun", back_populates="workflow", cascade="all, delete-orphan"
    )


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), nullable=False)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    project_id: Mapped[Optional[str]] = mapped_column(String(36))  # reserved, unused in MVP
    status: Mapped[str] = mapped_column(String(32), default="queued")
    input_summary: Mapped[dict | None] = mapped_column(JSON, default=dict)
    output_summary: Mapped[dict | None] = mapped_column(JSON, default=dict)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    workflow: Mapped[Workflow] = relationship("Workflow", back_populates="runs")
    steps: Mapped[list[WorkflowRunStep]] = relationship(
        "WorkflowRunStep", back_populates="run", cascade="all, delete-orphan", order_by="WorkflowRunStep.order"
    )


class WorkflowRunStep(Base):
    __tablename__ = "workflow_run_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), nullable=False)
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    step_name: Mapped[str] = mapped_column(String(64), nullable=False)
    worker_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    job_id: Mapped[Optional[str]] = mapped_column(String(36))  # -> Job.id when worker step
    parameters: Mapped[dict | None] = mapped_column(JSON, default=dict)
    result_path: Mapped[Optional[str]] = mapped_column(String(255))
    metrics: Mapped[dict | None] = mapped_column(JSON, default=dict)
    logs: Mapped[Optional[str]] = mapped_column(Text)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    run: Mapped[WorkflowRun] = relationship("WorkflowRun", back_populates="steps")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd portal/backend && .venv/bin/python -m pytest tests/test_models_workflow.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add portal/backend/app/models.py portal/backend/tests/test_models_workflow.py
git commit -m "feat(workflow): Workflow/WorkflowRun/WorkflowRunStep models"
```

---

## Task 2: RAPID DAG template + loader

**Files:**
- Create: `app/workflow/__init__.py`, `app/workflow/templates/rapid_v1.json`, `app/workflow/template_loader.py`
- Test: `tests/test_template_loader.py`

- [ ] **Step 1: Create the package + template**

Create `app/workflow/__init__.py` (empty).

Create `app/workflow/templates/rapid_v1.json`:

```json
{
  "template_key": "rapid_v1",
  "name": "RAPID Protein Design",
  "description": "FASTA/PDB → MSA → Conservation → ProteinMPNN → SoluProt → Validation → Report",
  "nodes": [
    {"id": "input", "step_name": "FASTA/PDB Input", "worker": "portal.input", "params": {}},
    {"id": "msa", "step_name": "MSA Search", "worker": "gateway.mmseqs", "params": {"max_seqs": ""}},
    {"id": "conservation", "step_name": "Conservation Mask", "worker": "portal.conservation", "params": {"tiers": [30, 50, 70]}},
    {"id": "mpnn", "step_name": "ProteinMPNN Design", "worker": "gateway.proteinmpnn", "params": {"num_seq_per_target": 16, "sampling_temp": 0.1, "seed": 0, "batch_size": 1}},
    {"id": "soluprot", "step_name": "SoluProt Filter", "worker": "portal.soluprot_mock", "params": {"top_k": 20}},
    {"id": "validate", "step_name": "Structure Validation", "worker": "gateway.esmfold", "params": {"plddt_cutoff": 85, "rmsd_cutoff": 2.0}},
    {"id": "report", "step_name": "Report Export", "worker": "portal.report", "params": {}}
  ],
  "edges": [
    {"from": "input", "to": "msa", "map": {"sequence": "sequence"}},
    {"from": "msa", "to": "conservation", "map": {"msa": "msa"}},
    {"from": "input", "to": "mpnn", "map": {"pdb": "backbone"}},
    {"from": "conservation", "to": "mpnn", "map": {"mask": "fixed_positions"}},
    {"from": "mpnn", "to": "soluprot", "map": {"fasta": "candidates"}},
    {"from": "soluprot", "to": "validate", "map": {"top_candidates": "sequences"}},
    {"from": "validate", "to": "report", "map": {"structures": "structures", "metrics": "metrics"}}
  ]
}
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_template_loader.py`:

```python
import pytest
from app.workflow import template_loader


def test_load_rapid_v1_returns_ordered_steps():
    tpl = template_loader.load_template("rapid_v1")
    assert tpl["template_key"] == "rapid_v1"
    steps = template_loader.ordered_steps(tpl)
    names = [s["step_name"] for s in steps]
    assert names == [
        "FASTA/PDB Input", "MSA Search", "Conservation Mask",
        "ProteinMPNN Design", "SoluProt Filter", "Structure Validation", "Report Export",
    ]


def test_unknown_template_raises():
    with pytest.raises(KeyError):
        template_loader.load_template("does_not_exist")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd portal/backend && .venv/bin/python -m pytest tests/test_template_loader.py -q`
Expected: FAIL (`ModuleNotFoundError: app.workflow.template_loader`).

- [ ] **Step 4: Implement the loader**

Create `app/workflow/template_loader.py`:

```python
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_TEMPLATE_DIR = Path(__file__).parent / "templates"


@lru_cache(maxsize=None)
def load_template(template_key: str) -> dict:
    """Load a DAG template JSON by key. Raises KeyError if missing."""
    path = _TEMPLATE_DIR / f"{template_key}.json"
    if not path.exists():
        raise KeyError(f"unknown workflow template: {template_key}")
    return json.loads(path.read_text(encoding="utf-8"))


def ordered_steps(template: dict) -> list[dict]:
    """Topologically order nodes by the edge list (linear DAG for rapid_v1)."""
    nodes = {n["id"]: n for n in template["nodes"]}
    incoming = {nid: 0 for nid in nodes}
    adj: dict[str, list[str]] = {nid: [] for nid in nodes}
    for e in template["edges"]:
        adj[e["from"]].append(e["to"])
        incoming[e["to"]] += 1
    # Kahn's algorithm, stable by declaration order
    ready = [nid for nid in nodes if incoming[nid] == 0]
    ordered: list[str] = []
    seen = set()
    while ready:
        nid = ready.pop(0)
        if nid in seen:
            continue
        seen.add(nid)
        ordered.append(nid)
        for nxt in adj[nid]:
            incoming[nxt] -= 1
            if incoming[nxt] == 0:
                ready.append(nxt)
    return [nodes[nid] for nid in ordered]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd portal/backend && .venv/bin/python -m pytest tests/test_template_loader.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add portal/backend/app/workflow/__init__.py portal/backend/app/workflow/templates/rapid_v1.json portal/backend/app/workflow/template_loader.py portal/backend/tests/test_template_loader.py
git commit -m "feat(workflow): rapid_v1 DAG template + loader"
```

---

## Task 3: Conservation mask (pure function)

**Files:**
- Create: `app/workflow/conservation.py`
- Test: `tests/test_conservation.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_conservation.py`:

```python
from app.workflow import conservation


def test_fully_conserved_column_is_in_all_tiers():
    # 3 identical sequences -> every column 100% conserved
    msa = ["ACDE", "ACDE", "ACDE"]
    mask = conservation.fixed_positions(msa, tiers=[30, 50, 70])
    # positions are 1-indexed; all 4 positions conserved at every tier
    assert mask["70"] == [1, 2, 3, 4]
    assert mask["30"] == [1, 2, 3, 4]


def test_variable_column_excluded_from_high_tier():
    msa = ["ACDE", "AGDE", "AHDE"]  # column 2 varies (C/G/H -> 33% identity)
    mask = conservation.fixed_positions(msa, tiers=[30, 50, 70])
    assert 2 not in mask["70"]      # 33% < 70%
    assert 2 in mask["30"]          # 33% >= 30%
    assert mask["70"] == [1, 3, 4]


def test_empty_msa_returns_empty_tiers():
    assert conservation.fixed_positions([], tiers=[30, 50, 70]) == {"30": [], "50": [], "70": []}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd portal/backend && .venv/bin/python -m pytest tests/test_conservation.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `app/workflow/conservation.py`:

```python
from __future__ import annotations

from collections import Counter


def column_identity(column: list[str]) -> float:
    """Fraction of the most common residue in a column (0..100)."""
    residues = [c for c in column if c not in ("-", ".", " ")]
    if not residues:
        return 0.0
    most = Counter(residues).most_common(1)[0][1]
    return 100.0 * most / len(residues)


def fixed_positions(msa: list[str], tiers: list[int]) -> dict[str, list[int]]:
    """Return, per conservation tier, the 1-indexed positions whose column
    identity >= tier. Positions are fixed (kept) during ProteinMPNN design."""
    result: dict[str, list[int]] = {str(t): [] for t in tiers}
    if not msa:
        return result
    width = min(len(s) for s in msa)
    for i in range(width):
        ident = column_identity([s[i] for s in msa])
        for t in tiers:
            if ident >= t:
                result[str(t)].append(i + 1)
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd portal/backend && .venv/bin/python -m pytest tests/test_conservation.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add portal/backend/app/workflow/conservation.py portal/backend/tests/test_conservation.py
git commit -m "feat(workflow): conservation mask from MSA"
```

---

## Task 4: Mock SoluProt scorer (pure function)

**Files:**
- Create: `app/workflow/soluprot_mock.py`
- Test: `tests/test_soluprot_mock.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_soluprot_mock.py`:

```python
from app.workflow import soluprot_mock


def test_score_is_deterministic_and_bounded():
    a = soluprot_mock.score("MKTAYIAKQR")
    b = soluprot_mock.score("MKTAYIAKQR")
    assert a == b
    assert 0.0 <= a <= 1.0


def test_filter_top_k_sorts_desc_and_truncates():
    candidates = [
        {"id": "s1", "sequence": "AAAA"},
        {"id": "s2", "sequence": "MKTAYIAKQR"},
        {"id": "s3", "sequence": "WWWWWWWW"},
    ]
    top = soluprot_mock.filter_top_k(candidates, top_k=2)
    assert len(top) == 2
    assert top[0]["soluprot_score"] >= top[1]["soluprot_score"]
    assert "soluprot_score" in top[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd portal/backend && .venv/bin/python -m pytest tests/test_soluprot_mock.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `app/workflow/soluprot_mock.py`:

```python
from __future__ import annotations

import hashlib

# Real-shaped interface: a future SolubilityClient.score(sequence) -> float in [0,1]
# would drop in here. MVP uses a deterministic hash so runs are reproducible.


def score(sequence: str) -> float:
    seq = (sequence or "").strip().upper()
    if not seq:
        return 0.0
    digest = hashlib.sha256(seq.encode()).digest()
    return int.from_bytes(digest[:4], "big") / 0xFFFFFFFF


def filter_top_k(candidates: list[dict], top_k: int) -> list[dict]:
    """Attach soluprot_score to each candidate, sort desc, keep top_k."""
    scored = [
        {**c, "soluprot_score": round(score(c.get("sequence", "")), 4)}
        for c in candidates
    ]
    scored.sort(key=lambda c: c["soluprot_score"], reverse=True)
    return scored[: max(0, int(top_k))]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd portal/backend && .venv/bin/python -m pytest tests/test_soluprot_mock.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add portal/backend/app/workflow/soluprot_mock.py portal/backend/tests/test_soluprot_mock.py
git commit -m "feat(workflow): mock SoluProt scorer + top_k filter"
```

---

## Task 5: Job bridge (create a portal Job for a worker step)

**Files:**
- Create: `app/workflow/job_bridge.py`
- Test: `tests/test_job_bridge.py`

Context: `app/runpod.py` exposes `pipeline_endpoint(key)`, `build_pipeline_payload(key, parameters, sequence, input_archive)`, and `RunpodClient().submit(endpoint_id, payload) -> runpod_job_id`. `app/storage.py` exposes `uploads_dir`, `build_archive`, `file_to_base64`. We reuse these so a step behaves exactly like a UI-submitted job and is polled by the existing `JobMonitor`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_job_bridge.py`:

```python
from pathlib import Path

from app import models
from app.database import Base, engine, SessionLocal
from app.workflow import job_bridge


class _FakeClient:
    def submit(self, endpoint_id, payload):
        _FakeClient.last = (endpoint_id, payload)
        return "rp-123"


def test_create_step_job_submits_and_persists(monkeypatch, tmp_path):
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(job_bridge, "RunpodClient", lambda: _FakeClient())
    monkeypatch.setattr(job_bridge, "pipeline_endpoint", lambda key: "endpoint-xyz")
    with SessionLocal() as db:
        user = models.User(username="jb_tester", password_hash="x")
        db.add(user)
        db.commit()
        pdb = tmp_path / "bb.pdb"
        pdb.write_text("ATOM      1  N   ALA A   1       0.0   0.0   0.0\n")
        job = job_bridge.create_step_job(
            db, user_id=user.id, title="mpnn step",
            pipeline="proteinmpnn", params={"num_seq_per_target": 4},
            input_files=[pdb], sequence=None,
        )
        assert job.pipeline == "proteinmpnn"
        assert job.endpoint_id == "endpoint-xyz"
        assert job.runpod_job_id == "rp-123"
        assert job.status in {"submitted", "in_queue", "pending"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd portal/backend && .venv/bin/python -m pytest tests/test_job_bridge.py -q`
Expected: FAIL (`ModuleNotFoundError: app.workflow.job_bridge`).

- [ ] **Step 3: Implement**

Create `app/workflow/job_bridge.py`:

```python
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from .. import models
from ..runpod import RunpodClient, build_pipeline_payload, pipeline_endpoint
from ..storage import build_archive, file_to_base64, uploads_dir


def create_step_job(
    db: Session,
    *,
    user_id: int,
    title: str,
    pipeline: str,
    params: dict,
    input_files: list[Path] | None = None,
    sequence: str | None = None,
) -> models.Job:
    """Create + submit a portal Job for a workflow step, reusing the gateway
    path so the existing JobMonitor drives it to completion."""
    job = models.Job(
        id=str(uuid4()), user_id=user_id, title=title, pipeline=pipeline,
        status="pending", parameters=params or {},
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    input_archive = None
    if input_files:
        root = uploads_dir(user_id, job.id)
        saved: list[Path] = []
        for src in input_files:
            dest = root / src.name
            dest.write_bytes(Path(src).read_bytes())
            saved.append(dest)
        archive_path = root / "inputs.tar.gz"
        build_archive(saved, archive_path, base_dir=root)
        input_archive = {
            "file_names": [p.name for p in saved],
            "kind": "uploaded",
            "archive_name": "inputs.tar.gz",
            "base64": file_to_base64(archive_path),
        }
        job.input_archive_path = str(archive_path)

    endpoint_id = pipeline_endpoint(pipeline)
    job.endpoint_id = endpoint_id
    payload = build_pipeline_payload(pipeline, parameters=params, sequence=sequence, input_archive=input_archive)
    runpod_job_id = RunpodClient().submit(endpoint_id, payload)
    job.runpod_job_id = runpod_job_id
    job.status = "submitted"
    db.commit()
    db.refresh(job)
    return job
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd portal/backend && .venv/bin/python -m pytest tests/test_job_bridge.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add portal/backend/app/workflow/job_bridge.py portal/backend/tests/test_job_bridge.py
git commit -m "feat(workflow): job_bridge creates+submits a portal Job per step"
```

---

## Task 6: Orchestrator (advance one step)

**Files:**
- Create: `app/workflow/orchestrator.py`
- Test: `tests/test_orchestrator.py`

The orchestrator exposes `start_run(db, run)` (mark running, create first step) and `advance(db, run)` (called by the monitor): it inspects the current running step; portal-side steps execute inline and complete immediately; worker steps are considered done when their linked `Job` reaches a terminal status, then their outputs are read and the next step is created. On any failure it marks the step + run failed and downstream steps skipped.

- [ ] **Step 1: Write the failing test** (portal-only happy path with a stub worker layer)

Create `tests/test_orchestrator.py`:

```python
from app import models
from app.database import Base, engine, SessionLocal
from app.workflow import orchestrator


def _seed_run(db):
    user = models.User(username="orch_tester", password_hash="x")
    db.add(user); db.commit()
    wf = models.Workflow(name="RAPID", template_key="rapid_v1", owner_id=user.id, dag={})
    db.add(wf); db.commit()
    run = models.WorkflowRun(
        workflow_id=wf.id, owner_id=user.id, status="queued",
        input_summary={"sequence": "ACDEFGHIKL", "msa": ["ACDEFGHIKL", "ACDEFGHIKL"]},
    )
    db.add(run); db.commit()
    return user, run


def test_start_run_creates_first_step(monkeypatch):
    Base.metadata.create_all(bind=engine)
    # Force every worker step to be treated as portal-inline for this unit test
    monkeypatch.setattr(orchestrator, "_is_worker_step", lambda node: False)
    monkeypatch.setattr(orchestrator, "_run_portal_step", lambda db, run, step, node: ({"done": True}, {}))
    with SessionLocal() as db:
        _user, run = _seed_run(db)
        orchestrator.start_run(db, run)
        assert run.status == "running"
        steps = db.query(models.WorkflowRunStep).filter_by(run_id=run.id).all()
        assert len(steps) == 1
        assert steps[0].step_name == "FASTA/PDB Input"


def test_advance_runs_all_portal_steps_to_completion(monkeypatch):
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(orchestrator, "_is_worker_step", lambda node: False)
    monkeypatch.setattr(orchestrator, "_run_portal_step", lambda db, run, step, node: ({"ok": True}, {"score": 1}))
    with SessionLocal() as db:
        _user, run = _seed_run(db)
        orchestrator.start_run(db, run)
        for _ in range(20):
            if run.status in {"completed", "failed"}:
                break
            orchestrator.advance(db, run)
        assert run.status == "completed"
        assert db.query(models.WorkflowRunStep).filter_by(run_id=run.id).count() == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd portal/backend && .venv/bin/python -m pytest tests/test_orchestrator.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `app/workflow/orchestrator.py`:

```python
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from .. import models
from . import conservation, soluprot_mock, template_loader

TERMINAL_OK = {"completed", "succeeded"}
TERMINAL_FAIL = {"failed", "cancelled", "timed_out", "error"}


def _nodes(run: models.WorkflowRun) -> list[dict]:
    tpl = template_loader.load_template(run.workflow.template_key)
    return template_loader.ordered_steps(tpl)


def _is_worker_step(node: dict) -> bool:
    return str(node.get("worker", "")).startswith("gateway.")


def start_run(db: Session, run: models.WorkflowRun) -> None:
    run.status = "running"
    run.started_at = datetime.utcnow()
    db.commit()
    _spawn_step(db, run, order=0)
    db.commit()
    # portal-inline first step (Input) completes immediately; keep advancing until a
    # worker step is pending or the run finishes.
    advance(db, run)


def _spawn_step(db: Session, run: models.WorkflowRun, order: int) -> models.WorkflowRunStep:
    node = _nodes(run)[order]
    step = models.WorkflowRunStep(
        run_id=run.id, order=order, step_name=node["step_name"],
        worker_name=node["worker"], status="running", parameters=node.get("params", {}),
    )
    db.add(step)
    db.commit()
    db.refresh(step)
    return step


def _current_step(db: Session, run: models.WorkflowRun) -> models.WorkflowRunStep | None:
    return (
        db.query(models.WorkflowRunStep)
        .filter_by(run_id=run.id, status="running")
        .order_by(models.WorkflowRunStep.order.desc())
        .first()
    )


def advance(db: Session, run: models.WorkflowRun) -> None:
    """Idempotent: progress the run by at most one transition."""
    if run.status not in {"running"}:
        return
    nodes = _nodes(run)
    step = _current_step(db, run)
    if step is None:
        return
    node = nodes[step.order]

    if _is_worker_step(node):
        job = db.query(models.Job).filter_by(id=step.job_id).first() if step.job_id else None
        if job is None:
            _submit_worker_step(db, run, step, node)
            return
        jstatus = (job.status or "").lower()
        if jstatus in TERMINAL_FAIL:
            _fail(db, run, step, job.error_message or "worker step failed")
            return
        if jstatus not in TERMINAL_OK:
            return  # still running; monitor will re-check
        result, metrics = _collect_worker_output(job)
    else:
        try:
            result, metrics = _run_portal_step(db, run, step, node)
        except Exception as exc:  # noqa: BLE001
            _fail(db, run, step, str(exc))
            return

    _complete_step(db, run, step, result, metrics)
    if step.order + 1 < len(nodes):
        _spawn_step(db, run, order=step.order + 1)
        db.commit()
        advance(db, run)  # portal-inline steps chain immediately
    else:
        run.status = "completed"
        run.finished_at = datetime.utcnow()
        run.output_summary = _build_output_summary(db, run)
        db.commit()


def _complete_step(db, run, step, result, metrics):
    step.status = "completed"
    step.metrics = metrics or {}
    step.finished_at = datetime.utcnow()
    run.input_summary = {**(run.input_summary or {}), **(result or {})}
    db.commit()


def _fail(db, run, step, message: str):
    step.status = "failed"
    step.error_message = message
    step.finished_at = datetime.utcnow()
    run.status = "failed"
    run.error_message = f"{step.step_name}: {message}"
    run.finished_at = datetime.utcnow()
    for later in db.query(models.WorkflowRunStep).filter(
        models.WorkflowRunStep.run_id == run.id, models.WorkflowRunStep.order > step.order
    ):
        later.status = "skipped"
    db.commit()


def _run_portal_step(db, run, step, node) -> tuple[dict, dict]:
    """Execute a portal-side step. Returns (result_dict_merged_into_summary, metrics)."""
    ctx = run.input_summary or {}
    worker = node["worker"]
    if worker == "portal.input":
        return {}, {}
    if worker == "portal.conservation":
        mask = conservation.fixed_positions(ctx.get("msa", []), node["params"]["tiers"])
        return {"mask": mask}, {"tiers": node["params"]["tiers"]}
    if worker == "portal.soluprot_mock":
        cands = ctx.get("candidates", [])
        top = soluprot_mock.filter_top_k(cands, node["params"]["top_k"])
        return {"top_candidates": top}, {"kept": len(top)}
    if worker == "portal.report":
        return {}, {"candidates": len(ctx.get("top_candidates", []))}
    raise ValueError(f"unknown portal worker: {worker}")


def _submit_worker_step(db, run, step, node) -> None:
    """Placeholder wired in Task 7b; raises until implemented."""
    raise NotImplementedError("worker step submission wired in Task 7b")


def _collect_worker_output(job) -> tuple[dict, dict]:
    """Placeholder wired in Task 7b."""
    return {}, {}


def _build_output_summary(db, run) -> dict:
    ctx = run.input_summary or {}
    return {"candidates": ctx.get("top_candidates", [])}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd portal/backend && .venv/bin/python -m pytest tests/test_orchestrator.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add portal/backend/app/workflow/orchestrator.py portal/backend/tests/test_orchestrator.py
git commit -m "feat(workflow): DAG orchestrator (portal-inline steps + failure propagation)"
```

---

## Task 7: Wire worker steps into the orchestrator

**Files:**
- Modify: `app/workflow/orchestrator.py`
- Test: `tests/test_orchestrator_worker.py`

- [ ] **Step 1: Write the failing test** (a worker step submits a Job, completes, advances)

Create `tests/test_orchestrator_worker.py`:

```python
from app import models
from app.database import Base, engine, SessionLocal
from app.workflow import orchestrator


def test_worker_step_submits_then_completes(monkeypatch):
    Base.metadata.create_all(bind=engine)
    created = {}

    def fake_submit(db, run, step, node):
        job = models.Job(user_id=run.owner_id, title=step.step_name, pipeline="mmseqs", status="submitted")
        db.add(job); db.commit(); db.refresh(job)
        step.job_id = job.id
        created["job"] = job
        db.commit()

    def fake_collect(job):
        return {"msa": ["ACDE", "ACDE"]}, {"n_seqs": 2}

    monkeypatch.setattr(orchestrator, "_submit_worker_step", fake_submit)
    monkeypatch.setattr(orchestrator, "_collect_worker_output", fake_collect)
    monkeypatch.setattr(orchestrator, "_run_portal_step", lambda db, run, step, node: ({}, {}))

    with SessionLocal() as db:
        user = models.User(username="wstep", password_hash="x"); db.add(user); db.commit()
        wf = models.Workflow(name="RAPID", template_key="rapid_v1", owner_id=user.id, dag={}); db.add(wf); db.commit()
        run = models.WorkflowRun(workflow_id=wf.id, owner_id=user.id, status="queued", input_summary={"sequence": "ACDE"})
        db.add(run); db.commit()
        orchestrator.start_run(db, run)  # Input(portal) -> MSA(worker) submitted, now waiting
        msa_step = db.query(models.WorkflowRunStep).filter_by(run_id=run.id, step_name="MSA Search").first()
        assert msa_step.status == "running" and msa_step.job_id
        # simulate the worker finishing
        created["job"].status = "completed"; db.commit()
        orchestrator.advance(db, run)
        db.refresh(msa_step)
        assert msa_step.status == "completed"
        assert msa_step.metrics == {"n_seqs": 2}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd portal/backend && .venv/bin/python -m pytest tests/test_orchestrator_worker.py -q`
Expected: FAIL (`NotImplementedError: worker step submission wired in Task 7b`).

- [ ] **Step 3: Implement the two placeholders**

In `app/workflow/orchestrator.py`, replace `_submit_worker_step` and `_collect_worker_output`:

```python
from pathlib import Path

from . import job_bridge
from ..storage import results_dir

# gateway.<pipeline> -> (pipeline key, input kind)
_WORKER_PIPELINE = {
    "gateway.mmseqs": "mmseqs",
    "gateway.proteinmpnn": "proteinmpnn",
    "gateway.esmfold": "esmfold",
}


def _submit_worker_step(db, run, step, node) -> None:
    pipeline = _WORKER_PIPELINE[node["worker"]]
    ctx = run.input_summary or {}
    sequence = None
    input_files: list[Path] = []
    if pipeline == "mmseqs":
        sequence = ctx.get("sequence")
    elif pipeline == "proteinmpnn":
        pdb_path = ctx.get("backbone_path")
        if not pdb_path:
            _fail(db, run, step, "ProteinMPNN needs a PDB backbone (upload a structure).")
            return
        input_files = [Path(pdb_path)]
    elif pipeline == "esmfold":
        top = ctx.get("top_candidates", [])
        sequence = top[0]["sequence"] if top else ctx.get("sequence")
    job = job_bridge.create_step_job(
        db, user_id=run.owner_id, title=f"{run.id[:8]} {step.step_name}",
        pipeline=pipeline, params=step.parameters or {},
        input_files=input_files, sequence=sequence,
    )
    step.job_id = job.id
    db.commit()


def _collect_worker_output(job) -> tuple[dict, dict]:
    """Read a completed step-Job's artifacts into the run context + metrics."""
    result: dict = {}
    metrics: dict = {}
    rdir = Path(job.result_dir) if job.result_dir else None
    if job.pipeline == "proteinmpnn" and rdir:
        fasta = next(iter(rdir.rglob("*.fa")), None) or next(iter(rdir.rglob("*.fasta")), None)
        if fasta:
            result["candidates"] = _parse_fasta_candidates(fasta.read_text())
            metrics["designs"] = len(result["candidates"])
    elif job.pipeline == "mmseqs" and rdir:
        a3m = next(iter(rdir.rglob("*.a3m")), None)
        if a3m:
            result["msa"] = _parse_a3m(a3m.read_text())
            metrics["n_seqs"] = len(result["msa"])
    elif job.pipeline == "esmfold" and rdir:
        pdb = next(iter(rdir.rglob("*ranked_0*.pdb")), None) or next(iter(rdir.rglob("*.pdb")), None)
        if pdb:
            result["structures"] = [str(pdb)]
            metrics["plddt"] = _mean_plddt(pdb.read_text())
    return result, metrics


def _parse_fasta_candidates(text: str) -> list[dict]:
    out, cur_id, cur = [], None, []
    for line in text.splitlines():
        if line.startswith(">"):
            if cur_id is not None:
                out.append({"id": cur_id, "sequence": "".join(cur)})
            cur_id, cur = line[1:].strip() or f"seq_{len(out)+1}", []
        elif line.strip():
            cur.append(line.strip())
    if cur_id is not None:
        out.append({"id": cur_id, "sequence": "".join(cur)})
    return out


def _parse_a3m(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln and not ln.startswith(">")]


def _mean_plddt(pdb_text: str) -> float | None:
    vals = []
    for ln in pdb_text.splitlines():
        if ln.startswith("ATOM") and len(ln) >= 66 and ln[12:16].strip() == "CA":
            try:
                vals.append(float(ln[60:66]))
            except ValueError:
                pass
    return round(sum(vals) / len(vals), 2) if vals else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd portal/backend && .venv/bin/python -m pytest tests/test_orchestrator_worker.py tests/test_orchestrator.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add portal/backend/app/workflow/orchestrator.py portal/backend/tests/test_orchestrator_worker.py
git commit -m "feat(workflow): wire mmseqs/proteinmpnn/esmfold worker steps"
```

---

## Task 8: WorkflowMonitor daemon + startup wiring

**Files:**
- Create: `app/workflow/monitor.py`
- Modify: `app/main.py`
- Test: `tests/test_workflow_monitor.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_workflow_monitor.py`:

```python
from app import models
from app.database import Base, engine, SessionLocal
from app.workflow import monitor, orchestrator


def test_monitor_tick_advances_running_runs(monkeypatch):
    Base.metadata.create_all(bind=engine)
    calls = {"n": 0}
    monkeypatch.setattr(orchestrator, "advance", lambda db, run: calls.__setitem__("n", calls["n"] + 1))
    with SessionLocal() as db:
        user = models.User(username="mon", password_hash="x"); db.add(user); db.commit()
        wf = models.Workflow(name="RAPID", template_key="rapid_v1", owner_id=user.id, dag={}); db.add(wf); db.commit()
        db.add(models.WorkflowRun(workflow_id=wf.id, owner_id=user.id, status="running")); db.commit()
    monitor.tick_once()
    assert calls["n"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd portal/backend && .venv/bin/python -m pytest tests/test_workflow_monitor.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement the monitor**

Create `app/workflow/monitor.py`:

```python
from __future__ import annotations

import threading

from ..config import get_settings
from ..database import SessionLocal
from .. import models
from . import orchestrator

settings = get_settings()


def tick_once() -> None:
    with SessionLocal() as db:
        runs = db.query(models.WorkflowRun).filter_by(status="running").all()
        for run in runs:
            try:
                orchestrator.advance(db, run)
            except Exception as exc:  # noqa: BLE001
                print(f"[wf-monitor] run {run.id} error: {exc}")


class WorkflowMonitor:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="workflow-monitor", daemon=True)

    def start(self) -> None:
        if not self.thread.is_alive():
            self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                tick_once()
            except Exception as exc:  # noqa: BLE001
                print(f"[wf-monitor] error: {exc}")
            finally:
                self._stop.wait(settings.poll_interval_seconds)


workflow_monitor = WorkflowMonitor()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd portal/backend && .venv/bin/python -m pytest tests/test_workflow_monitor.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Wire into main.py**

In `app/main.py`, add to imports: `from .workflow.monitor import workflow_monitor` and (later task) the router. Extend the startup/shutdown hooks:

```python
@app.on_event("startup")
def start_monitor() -> None:
    monitor.start()
    workflow_monitor.start()


@app.on_event("shutdown")
def stop_monitor() -> None:
    monitor.stop()
    workflow_monitor.stop()
```

- [ ] **Step 6: Commit**

```bash
git add portal/backend/app/workflow/monitor.py portal/backend/app/main.py portal/backend/tests/test_workflow_monitor.py
git commit -m "feat(workflow): WorkflowMonitor daemon + startup wiring"
```

---

## Task 9: Workflows API router

**Files:**
- Create: `app/routers/workflows.py`
- Modify: `app/routers/__init__.py`, `app/main.py`
- Test: `tests/test_workflows_api.py`

The router uses FastAPI `TestClient` with the existing `get_current_user` dependency overridden in the test.

- [ ] **Step 1: Write the failing test**

Create `tests/test_workflows_api.py`:

```python
from fastapi.testclient import TestClient

from app import models
from app.database import Base, engine, SessionLocal
from app.main import app
from app.auth import get_current_user


def _override_user():
    with SessionLocal() as db:
        u = db.query(models.User).filter_by(username="apiuser").first()
        if not u:
            u = models.User(username="apiuser", password_hash="x"); db.add(u); db.commit(); db.refresh(u)
        return u


def test_instantiate_and_list_workflow(monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_current_user] = _override_user
    client = TestClient(app)

    r = client.post("/api/workflows", json={"template_key": "rapid_v1", "name": "my rapid"})
    assert r.status_code == 200, r.text
    wf_id = r.json()["id"]

    r = client.get("/api/workflows")
    assert r.status_code == 200
    assert any(w["id"] == wf_id for w in r.json()["workflows"])

    app.dependency_overrides.clear()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd portal/backend && .venv/bin/python -m pytest tests/test_workflows_api.py -q`
Expected: FAIL (404 — router not registered).

- [ ] **Step 3: Implement the router**

Create `app/routers/workflows.py`:

```python
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models
from ..auth import get_current_user
from ..database import get_db
from ..workflow import orchestrator, template_loader

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


class InstantiateRequest(BaseModel):
    template_key: str = "rapid_v1"
    name: str | None = None
    description: str | None = None


class RunRequest(BaseModel):
    sequence: str | None = None
    backbone_path: str | None = None  # server path of an uploaded PDB (MVP)
    step_params: dict = {}


def _wf_dict(wf: models.Workflow, last_status: str | None) -> dict:
    return {
        "id": wf.id, "name": wf.name, "description": wf.description,
        "template_key": wf.template_key, "last_run_status": last_status,
        "created_at": wf.created_at.isoformat(),
    }


@router.get("")
def list_workflows(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    wfs = db.query(models.Workflow).filter_by(owner_id=user.id).order_by(models.Workflow.created_at.desc()).all()
    out = []
    for wf in wfs:
        last = db.query(models.WorkflowRun).filter_by(workflow_id=wf.id).order_by(models.WorkflowRun.created_at.desc()).first()
        out.append(_wf_dict(wf, last.status if last else None))
    return {"workflows": out}


@router.post("")
def instantiate(payload: InstantiateRequest, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    try:
        tpl = template_loader.load_template(payload.template_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown template.")
    wf = models.Workflow(
        name=payload.name or tpl.get("name", payload.template_key),
        description=payload.description or tpl.get("description"),
        owner_id=user.id, template_key=payload.template_key, dag=tpl,
    )
    db.add(wf); db.commit(); db.refresh(wf)
    return _wf_dict(wf, None)


@router.get("/{workflow_id}")
def get_workflow(workflow_id: str, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    wf = db.query(models.Workflow).filter_by(id=workflow_id, owner_id=user.id).first()
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    runs = db.query(models.WorkflowRun).filter_by(workflow_id=wf.id).order_by(models.WorkflowRun.created_at.desc()).all()
    return {**_wf_dict(wf, runs[0].status if runs else None), "dag": wf.dag,
            "runs": [{"id": r.id, "status": r.status, "created_at": r.created_at.isoformat()} for r in runs]}


@router.post("/{workflow_id}/runs")
def start_run(workflow_id: str, payload: RunRequest, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    wf = db.query(models.Workflow).filter_by(id=workflow_id, owner_id=user.id).first()
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    run = models.WorkflowRun(
        workflow_id=wf.id, owner_id=user.id, status="queued",
        input_summary={"sequence": payload.sequence, "backbone_path": payload.backbone_path},
    )
    db.add(run); db.commit(); db.refresh(run)
    orchestrator.start_run(db, run)
    return {"id": run.id, "status": run.status}


def _step_dict(s: models.WorkflowRunStep) -> dict:
    return {"order": s.order, "step_name": s.step_name, "worker_name": s.worker_name,
            "status": s.status, "job_id": s.job_id, "metrics": s.metrics,
            "error_message": s.error_message, "logs": s.logs}


@router.get("/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    run = db.query(models.WorkflowRun).filter_by(id=run_id, owner_id=user.id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    return {"id": run.id, "status": run.status, "error_message": run.error_message,
            "input_summary": run.input_summary, "output_summary": run.output_summary,
            "steps": [_step_dict(s) for s in run.steps]}


@router.get("/runs/{run_id}/report")
def get_report(run_id: str, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    run = db.query(models.WorkflowRun).filter_by(id=run_id, owner_id=user.id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    ctx = run.output_summary or {}
    return {"run_id": run.id, "status": run.status, "candidates": ctx.get("candidates", [])}


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    run = db.query(models.WorkflowRun).filter_by(id=run_id, owner_id=user.id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    if run.status in {"running", "queued"}:
        run.status = "cancelled"
        run.finished_at = datetime.utcnow()
        db.commit()
    return {"id": run.id, "status": run.status}
```

- [ ] **Step 4: Register the router**

In `app/routers/__init__.py` change the import line to include `workflows`:

```python
from . import assistant, auth, jobs, pipelines, rfdiffusion, users, workflows  # noqa: F401
```

In `app/main.py` add near the other imports `from .routers import ... workflows` and add:

```python
app.include_router(workflows.router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd portal/backend && .venv/bin/python -m pytest tests/test_workflows_api.py -q`
Expected: PASS (1 passed).

- [ ] **Step 6: Full backend suite + commit**

Run: `cd portal/backend && .venv/bin/python -m pytest tests/ -q`
Expected: all workflow tests pass (no regressions).

```bash
git add portal/backend/app/routers/workflows.py portal/backend/app/routers/__init__.py portal/backend/app/main.py portal/backend/tests/test_workflows_api.py
git commit -m "feat(workflow): /api/workflows router (instantiate/list/run/report/cancel)"
```

---

## Task 10: Frontend — API client types + fetchers

**Files:**
- Modify: `src/lib/api.ts`

- [ ] **Step 1: Add types + fetchers**

Append to `src/lib/api.ts` (mirror the existing `fetchPipelines`/`apiFetch` style):

```typescript
export interface WorkflowSummary {
  id: string;
  name: string;
  description?: string | null;
  template_key: string;
  last_run_status: string | null;
  created_at: string;
}

export interface WorkflowRunStep {
  order: number;
  step_name: string;
  worker_name: string;
  status: string;
  job_id: string | null;
  metrics: Record<string, unknown> | null;
  error_message: string | null;
  logs: string | null;
}

export interface WorkflowRunDetail {
  id: string;
  status: string;
  error_message: string | null;
  input_summary: Record<string, unknown> | null;
  output_summary: Record<string, unknown> | null;
  steps: WorkflowRunStep[];
}

export const listWorkflows = (token: string) =>
  apiFetch<{ workflows: WorkflowSummary[] }>("/api/workflows", token);

export const instantiateWorkflow = (token: string, body: { template_key: string; name?: string }) =>
  apiFetch<WorkflowSummary>("/api/workflows", token, { method: "POST", body: JSON.stringify(body) });

export const startWorkflowRun = (
  token: string,
  workflowId: string,
  body: { sequence?: string; backbone_path?: string; step_params?: Record<string, unknown> },
) => apiFetch<{ id: string; status: string }>(`/api/workflows/${workflowId}/runs`, token, { method: "POST", body: JSON.stringify(body) });

export const getWorkflowRun = (token: string, runId: string) =>
  apiFetch<WorkflowRunDetail>(`/api/workflows/runs/${runId}`, token);

export const getWorkflowReport = (token: string, runId: string) =>
  apiFetch<{ run_id: string; status: string; candidates: Array<Record<string, unknown>> }>(
    `/api/workflows/runs/${runId}/report`, token,
  );
```

- [ ] **Step 2: Typecheck**

Run: `cd portal/frontend && npx tsc --noEmit`
Expected: no new errors from api.ts.

- [ ] **Step 3: Commit**

```bash
git add portal/frontend/src/lib/api.ts
git commit -m "feat(workflow-ui): api client types + fetchers"
```

---

## Task 11: Frontend — workflow pages

**Files:**
- Create: `src/app/workflows/page.tsx`, `src/app/workflows/new/page.tsx`, `src/app/workflows/runs/[runId]/page.tsx`
- Modify: `src/app/page.tsx` (nav link)

Follow the existing `page.tsx` conventions: `"use client"`, read the SSO token the same way the home page does (reuse its token hook/context — check `src/app/page.tsx` for how `token` is obtained and replicate), Tailwind classes matching the current design (`rounded-2xl border border-slate-200`, `bg-brand-500`, etc.).

- [ ] **Step 1: List page**

Create `src/app/workflows/page.tsx`: a client component that on mount calls `listWorkflows(token)` and renders a table (name, description, `last_run_status` badge, created date) with a `상세` link to the run flow and an `+ RAPID 워크플로우 만들기` button linking to `/workflows/new`. Reuse the `JobStatusBadge` component for statuses if compatible; otherwise a simple coloured span.

- [ ] **Step 2: Create/run page**

Create `src/app/workflows/new/page.tsx`: instantiates the RAPID template (`instantiateWorkflow`), shows a per-step parameter form pre-filled from the template defaults (ProteinMPNN num_seq_per_target=16, temp=0.1, seed=0, batch=1; validation pLDDT=85, RMSD=2.0, top_k=20; conservation tiers 30/50/70), a sequence textarea and a PDB upload (reuse the existing upload → returns a server path; for MVP, upload the PDB via the existing `/api/jobs` upload path or a small `/api/workflows/upload` — if none exists, POST the file to a new `/api/workflows/upload` returning `{backbone_path}`; add that endpoint to `workflows.py` mirroring `save_uploads`). Then `startWorkflowRun` and redirect to the run page. Include a disabled `Langflow에서 편집 (Phase 2)` button.

- [ ] **Step 3: Run detail + report page**

Create `src/app/workflows/runs/[runId]/page.tsx`: polls `getWorkflowRun(token, runId)` every 5s until terminal; renders overall status, a vertical stepper of `steps` (status colour, metrics like pLDDT/SoluProt/kept, `error_message` shown in red for failed steps, `logs` in a collapsible), and when `completed` calls `getWorkflowReport` to show the candidate table (sequence, soluprot_score, plddt) with download links to the underlying step job artifacts (reuse existing job artifact download URL for the validation/mpnn job ids).

- [ ] **Step 4: Nav link**

In `src/app/page.tsx` header/nav, add a link to `/workflows` labelled `워크플로우` next to the existing controls.

- [ ] **Step 5: Build**

Run: `cd portal/frontend && npm run build`
Expected: `✓ Compiled successfully`, routes `/workflows`, `/workflows/new`, `/workflows/runs/[runId]` listed.

- [ ] **Step 6: Commit**

```bash
git add portal/frontend/src/app/workflows portal/frontend/src/app/page.tsx
git commit -m "feat(workflow-ui): list / create-run / run-detail + report pages"
```

---

## Task 12: Mock end-to-end + real ProteinMPNN verification

**Files:**
- Create: `tests/test_workflow_e2e_mock.py`

- [ ] **Step 1: Mock E2E test**

Create `tests/test_workflow_e2e_mock.py`: seed a run with a fake `backbone_path`, monkeypatch `job_bridge.create_step_job` to return a Job that is immediately `completed` with a `result_dir` containing a tiny ProteinMPNN FASTA, an a3m, and an ESMFold PDB fixture; then drive `orchestrator.start_run` + repeated `monitor.tick_once()` and assert the run reaches `completed` and `output_summary["candidates"]` is non-empty with `soluprot_score` present.

```python
# (fixtures written inline: a 2-record FASTA, a 2-line a3m, a 3-atom PDB with CA b-factors)
```

- [ ] **Step 2: Run mock E2E**

Run: `cd portal/backend && .venv/bin/python -m pytest tests/test_workflow_e2e_mock.py -q`
Expected: PASS (run completed, candidates present).

- [ ] **Step 3: Deploy + real ProteinMPNN E2E**

```bash
cd portal/frontend && npm run build
sudo systemctl restart bmp-backend bmp-frontend
```

Drive the deployed UI with Playwright (SSO creds in `portal/frontend/.env.production` `BOOTSTRAP_LOGIN_*`): create a RAPID workflow, upload a small PDB backbone, run, and confirm the ProteinMPNN step creates a real gateway job (`proteinmpnn`) that reaches a terminal state and yields candidate designs (mmseqs/esmfold may be slow — verify at least ProteinMPNN → mock SoluProt → report path). Capture the run detail showing per-step statuses.

- [ ] **Step 4: Commit**

```bash
git add portal/backend/tests/test_workflow_e2e_mock.py
git commit -m "test(workflow): mock end-to-end run reaches completed with candidates"
```

---

## Task 13: README

**Files:**
- Modify: `README.md` (repo root or `portal/README.md` — match where portal docs live)

- [ ] **Step 1: Document**

Add a "RAPID Workflow (SP1)" section: architecture (portal-orchestrated, steps = portal Jobs via gateway, WorkflowMonitor), the RAPID DAG + step→worker table, how to run one from the UI, which steps are real vs mock (SoluProt mock), config (`LANGFLOW_URL` reserved), and the Phase 2 (Langflow embed) + SP2/SP3/SP4 direction.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs(workflow): RAPID workflow MVP README section"
```

---

## Self-Review notes (addressed)

- **Spec coverage:** models (T1), DAG template (T2), conservation/soluprot/validation mapping (T3/T4/T7), orchestration + async via existing Job/JobMonitor (T5–T8), API (T9), UI list/create/run/report (T10–T11), error/failed-step display (T5 `_fail` + T11 step 3), logging/result_path/metrics persisted (T5–T7), testing mock→real (T12), README (T13), self-improvement hooks = structured run/step records (T1, satisfied by persistence). Langflow/project/SoluProt-real explicitly deferred per spec non-goals.
- **Placeholders:** the orchestrator's `_submit_worker_step`/`_collect_worker_output` are intentionally stubbed in T6 and implemented in T7 (test T6 asserts the stub raises; T7 replaces it) — not a plan placeholder.
- **Type consistency:** `create_step_job(...)`, `advance(db, run)`, `start_run(db, run)`, `tick_once()`, `fixed_positions(msa, tiers)`, `filter_top_k(candidates, top_k)`, `load_template`/`ordered_steps` names are used identically across tasks.
