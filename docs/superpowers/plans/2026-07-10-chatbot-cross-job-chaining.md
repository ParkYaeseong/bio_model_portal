# Chatbot Cross-Job Chaining + Connection Guide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the in-UI execution assistant feed a prior job's output artifacts into a new job (any compatible model pair, resolved server-side), and add an interactive `/chains` guide tab showing what connects to what.

**Architecture:** A single-source-of-truth compatibility module (`app/chaining.py`) declares each pipeline's produced/consumed roles. `run_model` gains `from_job_id`/`source_artifact_ids` params and uses `chaining.plan_chain` to inject the source job's structure files or extracted sequence. A `GET /api/chains/compat` endpoint returns the derived graph; a lightweight self-contained SVG page renders it plus curated example chains.

**Tech Stack:** FastAPI + SQLAlchemy (backend, pytest), Next.js App Router + SWR + Tailwind (frontend, no unit-test infra — verified via build).

**Conventions verified in this repo:**
- Backend tests run from `portal/backend/`: `python -m pytest tests/<file> -v`. Tests use `from app.database import SessionLocal` with `SessionLocal()` context managers and `monkeypatch` (see `tests/test_mcp_tools.py`).
- Completed jobs have `status == "completed"` (lowercased in `tasks.py`); active statuses are `{pending, submitted, running, queued, in_queue, in_progress, processing}` (see `mcp/tools.py:cancel_job`).
- `Artifact` rows: `job_id, file_name, file_path` (absolute on-disk path), `kind` (`structure` for `.pdb/.cif`).
- Frontend pages are `"use client"`, fetch through `@/lib/api` (`apiFetch`), pass `token = ""` (SSO via gateway). Nav links are inline `<Link>` in `app/page.tsx` header (Korean labels).

---

## File Structure

- **Create** `portal/backend/app/chaining.py` — compat metadata, `compatible_role`, `plan_chain`, `compat_graph`, `EXAMPLE_CHAINS`, `ChainError`.
- **Modify** `portal/backend/app/mcp/tools.py` — extend `run_model` schema + resolution.
- **Create** `portal/backend/app/routers/chains.py` — `GET /api/chains/compat`.
- **Modify** `portal/backend/app/main.py` — register the chains router.
- **Modify** `portal/backend/app/chat/loop.py` — SYSTEM_PROMPT chaining note.
- **Create** `portal/backend/tests/test_chaining.py`, `tests/test_chains_api.py`; **modify** `tests/test_mcp_tools.py`.
- **Modify** `portal/frontend/src/lib/api.ts` — `getChainsCompat` + types.
- **Create** `portal/frontend/src/app/chains/page.tsx` — interactive SVG graph + example cards.
- **Modify** `portal/frontend/src/app/page.tsx` — header link `연결 가이드`.

---

## Task 1: Compatibility module

**Files:**
- Create: `portal/backend/app/chaining.py`
- Test: `portal/backend/tests/test_chaining.py`

- [ ] **Step 1: Write the failing test**

Create `portal/backend/tests/test_chaining.py`:

```python
from app import chaining


def test_compatible_role_structure_files():
    assert chaining.compatible_role("rfdiffusion", "proteinmpnn") == ("structure", "files")
    assert chaining.compatible_role("colabfold", "diffdock") == ("structure", "files")


def test_compatible_role_sequence():
    assert chaining.compatible_role("proteinmpnn", "colabfold") == ("sequence", "sequence")


def test_incompatible_pair_returns_none():
    assert chaining.compatible_role("rfdiffusion", "mmseqs") is None
    assert chaining.compatible_role("diffdock", "colabfold") is None


def test_compatible_targets_lists_downstream():
    targets = chaining.compatible_targets("rfdiffusion")
    assert {"proteinmpnn", "diffdock", "rosetta_relax"}.issubset(set(targets))
    assert "rfdiffusion" not in targets  # no self


def test_compat_graph_shape():
    g = chaining.compat_graph()
    assert {n["key"] for n in g["nodes"]} >= {"rfdiffusion", "diffdock", "colabfold", "proteinmpnn"}
    assert {"from": "rfdiffusion", "to": "diffdock", "role": "structure"} in g["edges"]
    assert all(e["from"] != e["to"] for e in g["edges"])
    assert any(ex["steps"][0] == "rfdiffusion" for ex in g["examples"])


def test_first_fasta_sequence():
    text = ">seq1\nACDEF\nGHIKL\n>seq2\nZZZZ\n"
    assert chaining._first_fasta_sequence(text) == "ACDEFGHIKL"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /opt/bio_model_portal/portal/backend && python -m pytest tests/test_chaining.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.chaining'`

- [ ] **Step 3: Write minimal implementation**

Create `portal/backend/app/chaining.py`:

```python
"""Cross-job chaining: which pipeline outputs can feed which pipeline inputs.

Single source of truth used by both run_model (to resolve from_job_id into a
new job's inputs) and the /chains guide endpoint (to render the compat graph).
No file IO here except reading a source FASTA to extract a sequence; callers do
the input-file byte copying.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROLE_STRUCTURE = "structure"
ROLE_SEQUENCE = "sequence"
ROLE_COMPLEX = "complex"
ROLE_MSA = "msa"

# pipeline -> {"produces": [roles], "consumes": {role: delivery}}
# delivery: how a consumed role reaches the worker — "files" (uploaded input
# archive) or "sequence" (the payload `sequence` field).
CHAIN_META: dict[str, dict] = {
    "rfdiffusion":   {"produces": [ROLE_STRUCTURE], "consumes": {ROLE_STRUCTURE: "files"}},
    "proteinmpnn":   {"produces": [ROLE_SEQUENCE],  "consumes": {ROLE_STRUCTURE: "files"}},
    "colabfold":     {"produces": [ROLE_STRUCTURE], "consumes": {ROLE_SEQUENCE: "sequence"}},
    "alphafold":     {"produces": [ROLE_STRUCTURE], "consumes": {ROLE_SEQUENCE: "sequence"}},
    "esmfold":       {"produces": [ROLE_STRUCTURE], "consumes": {ROLE_SEQUENCE: "sequence"}},
    "esmfold2":      {"produces": [ROLE_STRUCTURE], "consumes": {ROLE_SEQUENCE: "sequence"}},
    "bioemu":        {"produces": [ROLE_STRUCTURE], "consumes": {ROLE_SEQUENCE: "sequence", ROLE_STRUCTURE: "files"}},
    "diffdock":      {"produces": [ROLE_COMPLEX],   "consumes": {ROLE_STRUCTURE: "files"}},
    "rosetta_relax": {"produces": [ROLE_STRUCTURE], "consumes": {ROLE_STRUCTURE: "files"}},
    "mmseqs":        {"produces": [ROLE_MSA],       "consumes": {ROLE_SEQUENCE: "sequence"}},
}

# artifact kinds that satisfy a role delivered via files
_ROLE_ARTIFACT_KINDS = {ROLE_STRUCTURE: {"structure"}}

EXAMPLE_CHAINS = [
    {"title": "De novo 결합체 설계", "steps": ["rfdiffusion", "proteinmpnn", "colabfold", "diffdock"],
     "prompt": "RFdiffusion으로 백본을 만들고, 그 결과로 ProteinMPNN 서열 설계, ColabFold로 접은 뒤 DiffDock으로 리간드를 도킹해줘."},
    {"title": "서열 설계 + 구조 검증", "steps": ["proteinmpnn", "colabfold"],
     "prompt": "이 ProteinMPNN 잡 결과 서열을 ColabFold로 접어줘."},
    {"title": "알려진 서열 폴딩 + 도킹", "steps": ["colabfold", "diffdock"],
     "prompt": "이 ColabFold 잡 구조로 DiffDock을 돌려줘. 리간드는 첨부할게."},
    {"title": "백본 직접 도킹", "steps": ["rfdiffusion", "diffdock"],
     "prompt": "이 RFdiffusion 백본으로 DiffDock을 돌려줘. 리간드는 첨부할게."},
]


class ChainError(Exception):
    """Raised when a requested chain is invalid; message is user-facing."""


@dataclass
class ChainPlan:
    delivery: str        # "files" | "sequence"
    artifacts: list      # models.Artifact rows (files delivery)
    sequence: str | None  # extracted sequence (sequence delivery)


def compatible_role(src_pipeline: str, dst_pipeline: str) -> tuple[str, str] | None:
    src = CHAIN_META.get(src_pipeline)
    dst = CHAIN_META.get(dst_pipeline)
    if not src or not dst:
        return None
    for role in src["produces"]:
        if role in dst["consumes"]:
            return role, dst["consumes"][role]
    return None


def compatible_targets(src_pipeline: str) -> list[str]:
    return [dst for dst in CHAIN_META
            if dst != src_pipeline and compatible_role(src_pipeline, dst)]


def _first_fasta_sequence(text: str) -> str:
    seq: list[str] = []
    started = False
    for line in text.splitlines():
        if line.startswith(">"):
            if started:
                break
            started = True
            continue
        if started:
            seq.append(line.strip())
    return "".join(seq)


def plan_chain(db, source_job, target_pipeline, source_artifact_ids=None) -> ChainPlan:
    """Decide how source_job's output feeds target_pipeline. Raises ChainError.

    Reads no bytes for files delivery (returns artifact rows; caller copies
    file_path). For sequence delivery it reads the source FASTA text only.
    """
    arts = list(source_job.artifacts)

    if source_artifact_ids:
        wanted = set(source_artifact_ids)
        chosen = [a for a in arts if a.id in wanted]
        missing = wanted - {a.id for a in chosen}
        if missing:
            raise ChainError(f"artifact(s) not found in source job: {sorted(missing)}")
        return ChainPlan(delivery="files", artifacts=chosen, sequence=None)

    role_delivery = compatible_role(source_job.pipeline, target_pipeline)
    if role_delivery is None:
        targets = compatible_targets(source_job.pipeline)
        raise ChainError(
            f"'{source_job.pipeline}' output cannot feed '{target_pipeline}'. "
            f"Compatible targets: {targets or 'none'}."
        )
    role, delivery = role_delivery

    if delivery == "files":
        kinds = _ROLE_ARTIFACT_KINDS.get(role, set())
        chosen = [a for a in arts if a.kind in kinds]
        if not chosen:
            raise ChainError(
                f"no {role} artifacts in source job; available: {[a.file_name for a in arts]}"
            )
        return ChainPlan(delivery="files", artifacts=chosen, sequence=None)

    fasta = next((a for a in arts if a.file_name.lower().endswith((".fa", ".fasta"))), None)
    if fasta is None:
        raise ChainError(
            f"no FASTA artifact to chain as sequence; available: {[a.file_name for a in arts]}"
        )
    seq = _first_fasta_sequence(Path(fasta.file_path).read_text())
    if not seq:
        raise ChainError(f"FASTA artifact '{fasta.file_name}' has no sequence")
    return ChainPlan(delivery="sequence", artifacts=[], sequence=seq)


def compat_graph() -> dict:
    nodes = [
        {"key": k, "produces": v["produces"], "consumes": list(v["consumes"].keys())}
        for k, v in CHAIN_META.items()
    ]
    edges = [
        {"from": src, "to": dst, "role": rd[0]}
        for src in CHAIN_META
        for dst in CHAIN_META
        if src != dst and (rd := compatible_role(src, dst))
    ]
    return {"nodes": nodes, "edges": edges, "examples": EXAMPLE_CHAINS}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /opt/bio_model_portal/portal/backend && python -m pytest tests/test_chaining.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
cd /opt/bio_model_portal
git add portal/backend/app/chaining.py portal/backend/tests/test_chaining.py
git commit -m "feat(chaining): compat model for cross-job artifact chaining"
```

---

## Task 2: Extend run_model with chaining resolution

**Files:**
- Modify: `portal/backend/app/mcp/tools.py` (imports, `run_model`, `TOOLS` schema)
- Test: `portal/backend/tests/test_mcp_tools.py`

- [ ] **Step 1: Write the failing tests**

Append to `portal/backend/tests/test_mcp_tools.py` (add `from pathlib import Path` at top):

```python
def _finished_job(db, user, pipeline, artifacts):
    """artifacts: list of (file_name, file_path, kind)."""
    j = models.Job(user_id=user.id, title="src", pipeline=pipeline, status="completed")
    db.add(j); db.commit(); db.refresh(j)
    for name, path, kind in artifacts:
        db.add(models.Artifact(job_id=j.id, file_name=name, file_path=str(path), kind=kind))
    db.commit(); db.refresh(j)
    return j


def _capture_create(monkeypatch, captured):
    def fake_create(db_, *, user_id, title, pipeline, params, input_files=None, sequence=None):
        captured["input_files"] = [Path(p).name for p in (input_files or [])]
        captured["sequence"] = sequence
        j = models.Job(user_id=user_id, title=title, pipeline=pipeline, status="submitted")
        db_.add(j); db_.commit(); db_.refresh(j)
        return j
    monkeypatch.setattr(tools.job_bridge, "create_step_job", fake_create)


def test_run_model_chains_structure_files(monkeypatch, tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        pdb = tmp_path / "backbone.pdb"; pdb.write_text("ATOM  1  N\n")
        src = _finished_job(db, u, "rfdiffusion", [("backbone.pdb", pdb, "structure")])
        captured = {}; _capture_create(monkeypatch, captured)
        r = tools.run_model(db, u, {"pipeline": "diffdock", "from_job_id": src.id})
        assert r["ok"] is True
        assert "backbone.pdb" in captured["input_files"]


def test_run_model_chains_sequence(monkeypatch, tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        fa = tmp_path / "designs.fasta"; fa.write_text(">d1\nACDEFGHIKL\n")
        src = _finished_job(db, u, "proteinmpnn", [("designs.fasta", fa, "generic")])
        captured = {}; _capture_create(monkeypatch, captured)
        r = tools.run_model(db, u, {"pipeline": "colabfold", "from_job_id": src.id})
        assert r["ok"] is True
        assert captured["sequence"] == "ACDEFGHIKL"


def test_run_model_chain_source_artifact_ids_override(monkeypatch, tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        pdb = tmp_path / "pick.pdb"; pdb.write_text("ATOM\n")
        other = tmp_path / "skip.pdb"; other.write_text("ATOM\n")
        src = _finished_job(db, u, "rfdiffusion",
                            [("pick.pdb", pdb, "structure"), ("skip.pdb", other, "structure")])
        picked = next(a.id for a in src.artifacts if a.file_name == "pick.pdb")
        captured = {}; _capture_create(monkeypatch, captured)
        r = tools.run_model(db, u, {"pipeline": "diffdock", "from_job_id": src.id,
                                    "source_artifact_ids": [picked]})
        assert r["ok"] is True
        assert captured["input_files"] == ["pick.pdb"]


def test_run_model_chain_rejects_other_users_job(monkeypatch, tmp_path):
    with SessionLocal() as db:
        u1, u2 = _user(db), _user(db)
        pdb = tmp_path / "b.pdb"; pdb.write_text("ATOM\n")
        src = _finished_job(db, u1, "rfdiffusion", [("b.pdb", pdb, "structure")])
        r = tools.run_model(db, u2, {"pipeline": "diffdock", "from_job_id": src.id})
        assert r["ok"] is False and "not found" in r["error"].lower()


def test_run_model_chain_rejects_unfinished_job(monkeypatch, tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        j = models.Job(user_id=u.id, title="run", pipeline="rfdiffusion", status="running")
        db.add(j); db.commit(); db.refresh(j)
        r = tools.run_model(db, u, {"pipeline": "diffdock", "from_job_id": j.id})
        assert r["ok"] is False and "not finished" in r["error"].lower()


def test_run_model_chain_incompatible_pair(monkeypatch, tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        pdb = tmp_path / "b.pdb"; pdb.write_text("ATOM\n")
        src = _finished_job(db, u, "rfdiffusion", [("b.pdb", pdb, "structure")])
        r = tools.run_model(db, u, {"pipeline": "mmseqs", "from_job_id": src.id})
        assert r["ok"] is False and "cannot feed" in r["error"].lower()


def test_run_model_chain_no_structure_artifacts(monkeypatch, tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        log = tmp_path / "stdout.log"; log.write_text("done\n")
        src = _finished_job(db, u, "rfdiffusion", [("stdout.log", log, "log")])
        r = tools.run_model(db, u, {"pipeline": "diffdock", "from_job_id": src.id})
        assert r["ok"] is False and "no structure artifacts" in r["error"].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /opt/bio_model_portal/portal/backend && python -m pytest tests/test_mcp_tools.py -v`
Expected: the 7 new tests FAIL (chaining not wired; `from_job_id` ignored so e.g. `input_files` empty / no error returned)

- [ ] **Step 3: Wire chaining into run_model**

In `portal/backend/app/mcp/tools.py`, add the import near the existing imports (after `from ..workflow import job_bridge`):

```python
from .. import chaining
```

Replace the body of `run_model` (currently `tools.py:38-69`) with:

```python
def run_model(db: Session, user: models.User, arguments: dict) -> dict:
    pipeline = str(arguments.get("pipeline") or "")
    if pipeline not in PIPELINES:
        return {"ok": False, "error": f"unknown pipeline '{pipeline}'. valid: {sorted(PIPELINES)}"}
    params = arguments.get("parameters") or {}
    sequence = arguments.get("sequence")
    from_job_id = arguments.get("from_job_id")
    source_artifact_ids = arguments.get("source_artifact_ids")

    input_files: list[Path] = []
    tmp: Path | None = None
    try:
        for item in arguments.get("files") or []:
            b64 = item.get("base64")
            if not b64:
                continue
            try:
                data = base64.b64decode(b64)
            except Exception:
                return {"ok": False, "error": "a file's base64 content is invalid"}
            if len(data) > _MAX_UPLOAD_BYTES:
                return {"ok": False, "error": f"file exceeds {_MAX_UPLOAD_BYTES} bytes"}
            tmp = tmp or Path(tempfile.mkdtemp(prefix="mcp_upload_"))
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(item.get("name") or "input"))
            dest = tmp / (safe or "input")
            dest.write_bytes(data)
            input_files.append(dest)

        if from_job_id or source_artifact_ids:
            if not from_job_id:
                return {"ok": False, "error": "from_job_id is required when source_artifact_ids is set"}
            src = _owned_job(db, user, from_job_id)
            if not src:
                return {"ok": False, "error": "source job not found"}
            active = {"pending", "submitted", "running", "queued", "in_queue", "in_progress", "processing"}
            if (src.status or "").lower() in active:
                return {"ok": False, "error": f"source job {src.id} is not finished (status={src.status})"}
            try:
                plan = chaining.plan_chain(db, src, pipeline, source_artifact_ids)
            except chaining.ChainError as exc:
                return {"ok": False, "error": str(exc)}
            if plan.delivery == "sequence":
                if not (sequence and str(sequence).strip()):
                    sequence = plan.sequence
            else:
                tmp = tmp or Path(tempfile.mkdtemp(prefix="mcp_chain_"))
                for artifact in plan.artifacts:
                    dest = tmp / artifact.file_name
                    dest.write_bytes(Path(artifact.file_path).read_bytes())
                    input_files.append(dest)

        job = job_bridge.create_step_job(
            db, user_id=user.id, title=f"mcp {pipeline}", pipeline=pipeline,
            params=params, input_files=input_files or None, sequence=sequence,
        )
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)  # bytes already copied into uploads_dir
    return {"ok": True, "job_id": job.id, "status": job.status, "endpoint_id": job.endpoint_id}
```

- [ ] **Step 4: Update the run_model tool schema**

In `portal/backend/app/mcp/tools.py`, replace the `"run_model"` entry in `TOOLS` (currently `tools.py:110-119`) with:

```python
    "run_model": (run_model,
        "Run a portal model. Use list_models first for valid pipeline keys and params. "
        "To use a previous job's output as this run's input (chaining, e.g. dock the backbone "
        "an RFdiffusion job produced), pass from_job_id=<that job's id>; the server injects its "
        "compatible outputs (a structure PDB is fed as an input file; a designed sequence is fed "
        "as the sequence). Optionally pass source_artifact_ids to pick specific artifacts. For "
        "DiffDock the ligand must still be provided via files or parameters.", {
        "type": "object",
        "properties": {
            "pipeline": {"type": "string"},
            "parameters": {"type": "object"},
            "sequence": {"type": "string"},
            "from_job_id": {"type": "string"},
            "source_artifact_ids": {"type": "array", "items": {"type": "string"}},
            "files": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "base64": {"type": "string"}}}},
        },
        "required": ["pipeline"],
    }),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /opt/bio_model_portal/portal/backend && python -m pytest tests/test_mcp_tools.py -v`
Expected: PASS (all, including the 4 original + 7 new)

- [ ] **Step 6: Commit**

```bash
cd /opt/bio_model_portal
git add portal/backend/app/mcp/tools.py portal/backend/tests/test_mcp_tools.py
git commit -m "feat(chat): run_model chains a prior job's output via from_job_id"
```

---

## Task 3: /api/chains/compat endpoint

**Files:**
- Create: `portal/backend/app/routers/chains.py`
- Modify: `portal/backend/app/main.py:11,36` (import + register)
- Test: `portal/backend/tests/test_chains_api.py`

- [ ] **Step 1: Write the failing test**

Create `portal/backend/tests/test_chains_api.py`:

```python
from app import chaining


def test_compat_endpoint_returns_graph(monkeypatch):
    # The router handler is a thin wrapper over chaining.compat_graph(); assert
    # the payload it returns is the derived graph with nodes/edges/examples.
    from app.routers import chains
    payload = chains.get_compat(current_user=object())
    assert payload == chaining.compat_graph()
    assert payload["nodes"] and payload["edges"] and payload["examples"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /opt/bio_model_portal/portal/backend && python -m pytest tests/test_chains_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.routers.chains'`

- [ ] **Step 3: Create the router**

Create `portal/backend/app/routers/chains.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import chaining, models
from ..auth import get_current_user

router = APIRouter(prefix="/api/chains", tags=["chains"])


@router.get("/compat")
def get_compat(current_user: models.User = Depends(get_current_user)) -> dict:
    """Return the model compatibility graph + curated example chains."""
    return chaining.compat_graph()
```

- [ ] **Step 4: Register the router in main.py**

In `portal/backend/app/main.py`, add `chains` to the routers import (line 11) so it reads:

```python
from .routers import assistant, auth, chains, chat, jobs, mcp_tokens, pipelines, rfdiffusion, selfimprove, users, workflows
```

And add the registration next to the other `include_router` calls (after `app.include_router(chat.router)` on line 33):

```python
app.include_router(chains.router)
```

- [ ] **Step 5: Run test + import check**

Run: `cd /opt/bio_model_portal/portal/backend && python -m pytest tests/test_chains_api.py -v && python -c "import app.main"`
Expected: PASS and no import error.

- [ ] **Step 6: Commit**

```bash
cd /opt/bio_model_portal
git add portal/backend/app/routers/chains.py portal/backend/app/main.py portal/backend/tests/test_chains_api.py
git commit -m "feat(api): GET /api/chains/compat returns model compat graph"
```

---

## Task 4: SYSTEM_PROMPT chaining note

**Files:**
- Modify: `portal/backend/app/chat/loop.py:19-27` (SYSTEM_PROMPT)
- Test: `portal/backend/tests/test_chat.py`

- [ ] **Step 1: Write the failing test**

Append to `portal/backend/tests/test_chat.py`:

```python
def test_system_prompt_mentions_chaining():
    from app.chat.loop import SYSTEM_PROMPT
    assert "from_job_id" in SYSTEM_PROMPT
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /opt/bio_model_portal/portal/backend && python -m pytest tests/test_chat.py::test_system_prompt_mentions_chaining -v`
Expected: FAIL — assertion error (`from_job_id` absent).

- [ ] **Step 3: Update SYSTEM_PROMPT**

In `portal/backend/app/chat/loop.py`, replace the `SYSTEM_PROMPT` assignment (lines 19-27) with:

```python
SYSTEM_PROMPT = (
    "You are the Bio Model Portal assistant. You help users run and interpret "
    "the portal's protein models (folding, docking, design, phage analysis). "
    "You can call tools to list models, run a model, check job status, fetch a "
    "job's results, and cancel a job. Call list_models first when you are unsure "
    "of a model's key or parameters. To chain jobs — use a finished job's output "
    "as the next run's input (e.g. dock the backbone an RFdiffusion job produced) "
    "— call run_model with from_job_id=<that job's id>; the server injects the "
    "compatible output (a structure as an input file, a designed sequence as the "
    "sequence). DiffDock still needs a ligand (SMILES/SDF) via files or parameters. "
    "If two models cannot connect, explain the compatible options. After running "
    "or fetching results, explain them clearly and concisely in the user's "
    "language. Never fabricate job IDs or results — always use the tools."
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /opt/bio_model_portal/portal/backend && python -m pytest tests/test_chat.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /opt/bio_model_portal
git add portal/backend/app/chat/loop.py portal/backend/tests/test_chat.py
git commit -m "feat(chat): tell the assistant how to chain jobs via from_job_id"
```

---

## Task 5: Frontend API client for compat graph

**Files:**
- Modify: `portal/frontend/src/lib/api.ts` (append types + function)

- [ ] **Step 1: Add the type and fetch function**

Append to `portal/frontend/src/lib/api.ts`:

```typescript
export type CompatNode = { key: string; produces: string[]; consumes: string[] };
export type CompatEdge = { from: string; to: string; role: string };
export type ExampleChain = { title: string; steps: string[]; prompt: string };
export type CompatGraph = { nodes: CompatNode[]; edges: CompatEdge[]; examples: ExampleChain[] };

export async function getChainsCompat(token?: string): Promise<CompatGraph> {
  return apiFetch<CompatGraph>("/api/chains/compat", token);
}
```

- [ ] **Step 2: Verify it type-checks**

Run: `cd /opt/bio_model_portal/portal/frontend && npx tsc --noEmit`
Expected: no errors (or only pre-existing unrelated ones — confirm no new error mentions `api.ts`).

- [ ] **Step 3: Commit**

```bash
cd /opt/bio_model_portal
git add portal/frontend/src/lib/api.ts
git commit -m "feat(frontend): getChainsCompat API client + types"
```

---

## Task 6: /chains guide page (interactive SVG graph + example cards)

**Files:**
- Create: `portal/frontend/src/app/chains/page.tsx`

- [ ] **Step 1: Create the page**

Create `portal/frontend/src/app/chains/page.tsx`:

```tsx
"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import useSWR from "swr";

import { CompatGraph, getChainsCompat } from "@/lib/api";

// Pipeline key -> short label for badges/nodes.
const LABELS: Record<string, string> = {
  rfdiffusion: "RFdiffusion",
  proteinmpnn: "ProteinMPNN",
  colabfold: "ColabFold",
  alphafold: "AlphaFold2",
  esmfold: "ESMFold",
  esmfold2: "ESMFold2",
  bioemu: "BioEmu",
  diffdock: "DiffDock",
  rosetta_relax: "Rosetta Relax",
  mmseqs: "MSA (mmseqs)",
};
const label = (k: string) => LABELS[k] ?? k;

// Lay nodes out on a circle so every edge is drawable without a graph lib.
function layout(keys: string[], cx: number, cy: number, r: number) {
  const pos: Record<string, { x: number; y: number }> = {};
  keys.forEach((k, i) => {
    const a = (2 * Math.PI * i) / keys.length - Math.PI / 2;
    pos[k] = { x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) };
  });
  return pos;
}

export default function ChainsPage() {
  const token = ""; // SSO via gateway header, like other pages
  const { data, isLoading, error } = useSWR<CompatGraph>(["chains-compat"], () => getChainsCompat(token));
  const [selected, setSelected] = useState<string | null>(null);

  const W = 720, H = 720, CX = 360, CY = 360, R = 260;
  const keys = useMemo(() => (data ? data.nodes.map((n) => n.key) : []), [data]);
  const pos = useMemo(() => layout(keys, CX, CY, R), [keys]);

  const edges = data?.edges ?? [];
  const isActiveEdge = (from: string) => selected === null || from === selected;
  const examplesForSelected = (data?.examples ?? []).filter(
    (ex) => selected === null || ex.steps.includes(selected)
  );

  return (
    <main className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white/80 px-6 py-5 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between">
          <div>
            <p className="text-sm text-slate-500">Bio Model Portal</p>
            <h1 className="text-2xl font-semibold text-slate-900">연결 가이드</h1>
          </div>
          <Link href="/" className="rounded-full border border-slate-200 px-5 py-2 text-sm text-slate-600 hover:bg-slate-100">
            돌아가기
          </Link>
        </div>
      </header>

      <section className="mx-auto grid max-w-7xl gap-6 px-6 py-8 lg:grid-cols-[720px_1fr]">
        <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          <p className="mb-2 text-sm text-slate-500">
            노드를 클릭하면 그 모델의 출력이 이어질 수 있는 대상이 강조됩니다.
          </p>
          {isLoading && <p className="p-8 text-slate-500">불러오는 중…</p>}
          {error && <p className="p-8 text-red-600">그래프를 불러오지 못했습니다.</p>}
          {data && (
            <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full">
              {edges.map((e, i) => {
                const a = pos[e.from], b = pos[e.to];
                if (!a || !b) return null;
                return (
                  <line
                    key={i}
                    x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                    stroke={isActiveEdge(e.from) ? "#6366f1" : "#e2e8f0"}
                    strokeWidth={isActiveEdge(e.from) ? 1.5 : 0.75}
                    markerEnd="url(#arrow)"
                  />
                );
              })}
              <defs>
                <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
                  <path d="M0,0 L7,3 L0,6 Z" fill="#6366f1" />
                </marker>
              </defs>
              {keys.map((k) => {
                const p = pos[k];
                const on = selected === k;
                return (
                  <g key={k} className="cursor-pointer" onClick={() => setSelected(on ? null : k)}>
                    <circle cx={p.x} cy={p.y} r={on ? 30 : 26}
                      fill={on ? "#6366f1" : "#fff"} stroke="#6366f1" strokeWidth={1.5} />
                    <text x={p.x} y={p.y + 44} textAnchor="middle"
                      className="text-[11px]" fill="#334155">{label(k)}</text>
                  </g>
                );
              })}
            </svg>
          )}
        </div>

        <div className="space-y-4">
          <h2 className="text-lg font-semibold text-slate-900">예시 체인</h2>
          {examplesForSelected.map((ex) => (
            <div key={ex.title} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <h3 className="font-semibold text-slate-900">{ex.title}</h3>
              <div className="mt-2 flex flex-wrap items-center gap-1 text-sm">
                {ex.steps.map((s, i) => (
                  <span key={s} className="flex items-center gap-1">
                    <span className="rounded-full bg-brand-100 px-3 py-1 text-brand-700">{label(s)}</span>
                    {i < ex.steps.length - 1 && <span className="text-slate-400">→</span>}
                  </span>
                ))}
              </div>
              <p className="mt-3 rounded-lg bg-slate-50 p-3 text-sm text-slate-600">
                <span className="font-medium text-slate-500">도우미에게: </span>“{ex.prompt}”
              </p>
            </div>
          ))}
          {examplesForSelected.length === 0 && (
            <p className="text-sm text-slate-500">선택한 모델이 포함된 예시가 없습니다.</p>
          )}
        </div>
      </section>
    </main>
  );
}
```

- [ ] **Step 2: Type-check**

Run: `cd /opt/bio_model_portal/portal/frontend && npx tsc --noEmit`
Expected: no new errors mentioning `chains/page.tsx`.

- [ ] **Step 3: Commit**

```bash
cd /opt/bio_model_portal
git add portal/frontend/src/app/chains/page.tsx
git commit -m "feat(frontend): /chains interactive connection guide"
```

---

## Task 7: Header nav link

**Files:**
- Modify: `portal/frontend/src/app/page.tsx:596-602` (header actions)

- [ ] **Step 1: Add the link**

In `portal/frontend/src/app/page.tsx`, immediately before the existing `<Link href="/mcp" …>AI 연결</Link>` (currently lines 597-602), insert:

```tsx
            <Link
              href="/chains"
              className="rounded-full border border-slate-200 px-5 py-2 text-sm text-slate-600 hover:bg-slate-100"
            >
              연결 가이드
            </Link>
```

- [ ] **Step 2: Build the frontend**

Run: `cd /opt/bio_model_portal/portal/frontend && npm run build`
Expected: build succeeds; `/chains` appears in the route list.

- [ ] **Step 3: Commit**

```bash
cd /opt/bio_model_portal
git add portal/frontend/src/app/page.tsx
git commit -m "feat(frontend): link 연결 가이드 in portal header"
```

---

## Task 8: End-to-end verification

- [ ] **Step 1: Full backend test suite**

Run: `cd /opt/bio_model_portal/portal/backend && python -m pytest tests/test_chaining.py tests/test_mcp_tools.py tests/test_chains_api.py tests/test_chat.py -v`
Expected: all PASS.

- [ ] **Step 2: Drive the real chat path (verify skill)**

Use the `verify` skill (or a manual smoke) to confirm: with a finished RFdiffusion-style job that has a `structure` artifact, a chat turn asking to DiffDock it results in a `run_model` call carrying `from_job_id`, and `create_step_job` receives the backbone PDB as an input file. Confirm `/chains` renders the graph and example cards in the browser and the header link navigates to it.

- [ ] **Step 3: Final commit / branch summary**

```bash
cd /opt/bio_model_portal && git log --oneline feat/chatbot-cross-job-chaining -8
```

---

## Self-Review Notes

- **Spec coverage:** compat model (Task 1) ↔ spec §1; run_model resolution incl. all error cases (Task 2) ↔ §2 + Error Handling; compat endpoint (Task 3) + guide page/graph/examples (Tasks 5-7) ↔ §3; SYSTEM_PROMPT (Task 4) ↔ §4; tests (Tasks 1-4, 8) ↔ §Testing. Non-goals (auto-wait, DAG, editor) intentionally absent.
- **Signature consistency:** `plan_chain(db, source_job, target_pipeline, source_artifact_ids)` returns `ChainPlan(delivery, artifacts, sequence)`, consumed identically in Task 2. `compat_graph()` shape (`nodes/edges/examples`) matches the frontend `CompatGraph` type. `getChainsCompat` path `/api/chains/compat` matches the router prefix+route.
- **Frontend tests:** repo has no JS test infra (no test script / vitest / jest); per YAGNI, correctness is covered by backend tests + tsc/build + the verify smoke, not a new frontend test harness.
