# Self-Improvement v1 (SP4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture chatbot interactions + model-run outcomes globally (anonymized), analyze them on a schedule into improvement artifacts (recommended defaults, failure warnings, recipes), and inject the *admin-approved* artifact back into the chatbot system prompt.

**Architecture:** New `app/selfimprove/` package in the FastAPI backend + two DB tables (`interactions`, `improvement_artifacts`). `/api/chat` records each turn (best-effort); a daemon-thread scheduler recomputes a `proposed` artifact on an interval; an admin endpoint promotes one to `active`; `loop.run_chat` injects the active artifact into the system prompt. Global-first, aggregate/anonymized, human-approval gate.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 (`Mapped`/`mapped_column`, sqlite `JSON`), pydantic-settings, daemon `threading.Thread` (mirrors `app/workflow/monitor.py`), pytest.

**Working directory:** `/opt/bio_model_portal/portal/backend`. Run tests with `.venv/bin/python -m pytest`.

**Conventions to follow:**
- Tests live in `tests/`, use `from app... import`, and get a clean DB per test via the autouse `_isolate_db` fixture in `conftest.py`. Make a user with `models.User(username=f"x_{uuid.uuid4().hex[:8]}", password_hash="x")`.
- The test suite must stay deterministic: the scheduler must be OFF under tests (`SELFIMPROVE_ENABLED` defaults such that tests don't start a thread — see Task 1 & 8).

---

### Task 1: Self-improvement settings

**Files:**
- Modify: `app/config.py` (add fields to `Settings`, after the `openai_model` field ~line 38)

- [ ] **Step 1: Add settings fields**

Add inside `class Settings` (after `openai_model`):

```python
    # --- Self-improvement (SP4) ---
    selfimprove_enabled: bool = Field(default=True, env="SELFIMPROVE_ENABLED")
    selfimprove_interval_s: int = Field(default=86400, env="SELFIMPROVE_INTERVAL_S")
    selfimprove_autoactivate: bool = Field(default=False, env="SELFIMPROVE_AUTOACTIVATE")
    # Comma-separated User.username values (e.g. "sso:<sub>,kbfportal") allowed to
    # activate/reject artifacts. Empty => nobody can promote (fail closed).
    selfimprove_admin_users: str = Field(default="", env="SELFIMPROVE_ADMIN_USERS")
```

- [ ] **Step 2: Verify import still works**

Run: `.venv/bin/python -c "from app.config import get_settings; s=get_settings(); print(s.selfimprove_enabled, s.selfimprove_interval_s, s.selfimprove_admin_users)"`
Expected: `True 86400 ` (empty admin users)

- [ ] **Step 3: Commit**

```bash
git add app/config.py
git commit -m "feat(selfimprove): settings (enabled/interval/autoactivate/admin_users)"
```

---

### Task 2: DB models — Interaction + ImprovementArtifact

**Files:**
- Modify: `app/models.py` (append two classes at end of file)
- Test: `tests/test_selfimprove_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_selfimprove_models.py
import uuid
from app import models
from app.database import SessionLocal


def _user(db):
    u = models.User(username=f"si_{uuid.uuid4().hex[:8]}", password_hash="x")
    db.add(u); db.commit(); db.refresh(u)
    return u


def test_interaction_persists():
    with SessionLocal() as db:
        u = _user(db)
        it = models.Interaction(
            user_id=u.id, provider="anthropic", model="claude-opus-4-8",
            user_message_len=12, reply_len=345,
            tool_calls=[{"name": "run_model", "arguments_sanitized": {"pipeline": "esmfold"}, "ok": True}],
            job_ids=["job-1"], error=None,
        )
        db.add(it); db.commit(); db.refresh(it)
        assert it.id and it.created_at is not None
        got = db.query(models.Interaction).filter_by(id=it.id).first()
        assert got.tool_calls[0]["name"] == "run_model"
        assert got.job_ids == ["job-1"]


def test_artifact_persists_with_status():
    with SessionLocal() as db:
        a = models.ImprovementArtifact(
            status="proposed", summary="test",
            payload={"recommended_defaults": {}, "warnings": [], "recipes": []},
            stats={"n_jobs": 0},
        )
        db.add(a); db.commit(); db.refresh(a)
        assert a.id and a.status == "proposed"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_selfimprove_models.py -q`
Expected: FAIL — `AttributeError: module 'app.models' has no attribute 'Interaction'`

- [ ] **Step 3: Implement the models**

Append to `app/models.py`:

```python
class Interaction(Base):
    __tablename__ = "interactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    user_message_len: Mapped[int] = mapped_column(Integer, default=0)
    reply_len: Mapped[int] = mapped_column(Integer, default=0)
    # [{name, arguments_sanitized(dict), ok(bool)}] — never raw base64/sequences.
    tool_calls: Mapped[list | None] = mapped_column(JSON, default=list)
    job_ids: Mapped[list | None] = mapped_column(JSON, default=list)
    error: Mapped[Optional[str]] = mapped_column(Text)


class ImprovementArtifact(Base):
    __tablename__ = "improvement_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    status: Mapped[str] = mapped_column(String(16), default="proposed")  # proposed|active|rejected
    summary: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict | None] = mapped_column(JSON, default=dict)
    stats: Mapped[dict | None] = mapped_column(JSON, default=dict)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_selfimprove_models.py -q`
Expected: PASS (2 passed). Tables auto-create via `Base.metadata.create_all` in the `_isolate_db` fixture.

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_selfimprove_models.py
git commit -m "feat(selfimprove): Interaction + ImprovementArtifact models"
```

---

### Task 3: Capture — sanitize + record_interaction, hook into /api/chat

**Files:**
- Create: `app/selfimprove/__init__.py` (empty)
- Create: `app/selfimprove/capture.py`
- Modify: `app/routers/chat.py` (call capture after `run_chat`)
- Test: `tests/test_selfimprove_capture.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_selfimprove_capture.py
import uuid
from app import models
from app.database import SessionLocal
from app.selfimprove import capture


def _user(db):
    u = models.User(username=f"cap_{uuid.uuid4().hex[:8]}", password_hash="x")
    db.add(u); db.commit(); db.refresh(u)
    return u


def test_sanitize_drops_base64_and_truncates_long_strings():
    calls = [{
        "name": "run_model",
        "arguments": {"pipeline": "proteinmpnn", "files": [{"name": "x.pdb", "base64": "AAAA"}], "note": "z" * 900},
        "result": {"ok": True, "job_id": "j1"},
    }]
    out = capture.sanitize_tool_calls(calls)
    assert out[0]["name"] == "run_model"
    assert out[0]["ok"] is True
    assert "files" not in out[0]["arguments_sanitized"]
    assert out[0]["arguments_sanitized"]["pipeline"] == "proteinmpnn"
    # long string replaced by a length marker, not the raw content
    assert out[0]["arguments_sanitized"]["note"] == "<str:900>"


def test_record_interaction_extracts_job_ids_and_lengths():
    with SessionLocal() as db:
        u = _user(db)
        history = [{"role": "user", "content": "run it"}]
        result = {
            "reply": "done",
            "tool_calls": [
                {"name": "list_models", "arguments": {}, "result": {"ok": True}},
                {"name": "run_model", "arguments": {"pipeline": "esmfold", "files": [{"name": "a", "base64": "QQ=="}]},
                 "result": {"ok": True, "job_id": "job-xyz"}},
            ],
            "model": "claude-opus-4-8",
        }
        capture.record_interaction(db, u, "anthropic", "claude-opus-4-8", history, result)
        it = db.query(models.Interaction).filter_by(user_id=u.id).first()
        assert it is not None
        assert it.user_message_len == len("run it")
        assert it.reply_len == len("done")
        assert it.job_ids == ["job-xyz"]
        names = [c["name"] for c in it.tool_calls]
        assert names == ["list_models", "run_model"]
        assert all("base64" not in (c["arguments_sanitized"].get("files") or [{}])[0] for c in it.tool_calls if c["name"] == "run_model")


def test_record_interaction_never_raises(monkeypatch):
    # A broken db must not propagate — capture is best-effort.
    class Boom:
        def add(self, *a): raise RuntimeError("db down")
    capture.record_interaction(Boom(), None, "anthropic", "m", [{"role": "user", "content": "x"}], {"reply": "y", "tool_calls": []})
    # no exception == pass
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_selfimprove_capture.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.selfimprove'`

- [ ] **Step 3: Implement capture**

Create `app/selfimprove/__init__.py` (empty file).

Create `app/selfimprove/capture.py`:

```python
from __future__ import annotations

from sqlalchemy.orm import Session

from .. import models

_MAX_STR = 512


def _sanitize_value(v):
    if isinstance(v, str):
        return v if len(v) <= _MAX_STR else f"<str:{len(v)}>"
    if isinstance(v, list):
        return [_sanitize_value(x) for x in v]
    if isinstance(v, dict):
        return {k: _sanitize_value(x) for k, x in v.items() if k != "base64"}
    return v


def sanitize_tool_calls(tool_calls: list[dict]) -> list[dict]:
    """Strip file base64 and truncate long strings; keep name/args/ok only."""
    out = []
    for c in tool_calls or []:
        result = c.get("result") or {}
        out.append({
            "name": c.get("name"),
            "arguments_sanitized": _sanitize_value(c.get("arguments") or {}),
            "ok": result.get("ok") is not False,
        })
    return out


def _job_ids(tool_calls: list[dict]) -> list[str]:
    ids = []
    for c in tool_calls or []:
        if c.get("name") == "run_model":
            jid = (c.get("result") or {}).get("job_id")
            if jid:
                ids.append(jid)
    return ids


def record_interaction(db: Session, user, provider: str, model: str, history: list[dict], result: dict) -> None:
    """Best-effort: record one assistant turn. Never raises."""
    try:
        last_user = ""
        for m in reversed(history or []):
            if m.get("role") == "user":
                last_user = m.get("content") or ""
                break
        tool_calls = result.get("tool_calls") or []
        it = models.Interaction(
            user_id=getattr(user, "id", None),
            provider=provider,
            model=model,
            user_message_len=len(last_user),
            reply_len=len(result.get("reply") or ""),
            tool_calls=sanitize_tool_calls(tool_calls),
            job_ids=_job_ids(tool_calls),
            error=None,
        )
        db.add(it)
        db.commit()
    except Exception as exc:  # noqa: BLE001 — capture must never break chat
        print(f"[selfimprove] capture failed: {exc}")
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
```

- [ ] **Step 4: Hook into the chat router**

In `app/routers/chat.py`, add the import near the other imports:

```python
from ..selfimprove import capture as si_capture
```

Then in `chat()`, replace the success path so it records after `run_chat`:

```python
    try:
        result = chat_loop.run_chat(db, current_user, provider, api_key, model, history, attachments=attachments)
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=f"LLM request failed: {exc}") from exc
    si_capture.record_interaction(db, current_user, payload.provider, result["model"], history, result)
    return ChatResponse(**result)
```

(Note: pass the *undecorated* `history` — the note/context decoration added earlier for attachments is fine to include; `record_interaction` only reads message lengths and role, and stores lengths, not text.)

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_selfimprove_capture.py -q`
Expected: PASS (3 passed).

- [ ] **Step 6: Run the full suite (no regressions)**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add app/selfimprove/__init__.py app/selfimprove/capture.py app/routers/chat.py tests/test_selfimprove_capture.py
git commit -m "feat(selfimprove): capture layer + /api/chat hook (best-effort, anonymized)"
```

---

### Task 4: Outcome reading (live join over jobs)

**Files:**
- Create: `app/selfimprove/outcomes.py`
- Test: `tests/test_selfimprove_outcomes.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_selfimprove_outcomes.py
import json, uuid
from pathlib import Path
from app import models
from app.database import SessionLocal
from app.selfimprove import outcomes


def _user(db):
    u = models.User(username=f"out_{uuid.uuid4().hex[:8]}", password_hash="x")
    db.add(u); db.commit(); db.refresh(u)
    return u


def test_job_outcome_success_and_missing():
    with SessionLocal() as db:
        u = _user(db)
        j = models.Job(user_id=u.id, title="t", pipeline="esmfold", status="completed")
        db.add(j); db.commit(); db.refresh(j)
        out = outcomes.job_outcome(db, j.id)
        assert out["status"] == "completed" and out["success"] is True
        missing = outcomes.job_outcome(db, "nope")
        assert missing["status"] is None and missing["success"] is False


def test_job_metrics_reads_plddt_from_output_json(tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        d = tmp_path / "res"; d.mkdir()
        (d / "output.json").write_text(json.dumps({"plddt": 88.5, "iptm": 0.7}))
        j = models.Job(user_id=u.id, title="t", pipeline="colabfold", status="completed", result_dir=str(d))
        db.add(j); db.commit(); db.refresh(j)
        m = outcomes.job_metrics(j)
        assert round(m["plddt"], 1) == 88.5 and round(m["iptm"], 1) == 0.7
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_selfimprove_outcomes.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.selfimprove.outcomes'`

- [ ] **Step 3: Implement outcomes**

Create `app/selfimprove/outcomes.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from .. import models

_METRIC_KEYS = ("plddt", "mean_plddt", "ptm", "iptm", "pae")
_SUCCESS = "completed"


def job_metrics(job: models.Job) -> dict:
    """Best-effort scalar metrics parsed from the job's output.json, if any."""
    metrics: dict = {}
    base = Path(job.result_dir or "")
    if not base.exists():
        return metrics
    for path in base.rglob("output.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, dict):
            for k in _METRIC_KEYS:
                v = data.get(k)
                if isinstance(v, (int, float)):
                    metrics.setdefault(k, float(v))
        break
    return metrics


def job_outcome(db: Session, job_id: str) -> dict:
    """Live read of a job's terminal outcome. success == status 'completed'."""
    job = db.query(models.Job).filter_by(id=str(job_id or "")).first()
    if not job:
        return {"status": None, "success": False, "metrics": {}}
    return {
        "status": job.status,
        "success": (job.status or "").lower() == _SUCCESS,
        "metrics": job_metrics(job),
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_selfimprove_outcomes.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add app/selfimprove/outcomes.py tests/test_selfimprove_outcomes.py
git commit -m "feat(selfimprove): live job outcome + metrics reader"
```

---

### Task 5: Analysis — compute_artifact

**Files:**
- Create: `app/selfimprove/analyze.py`
- Test: `tests/test_selfimprove_analyze.py`

**Design of `compute_artifact(db)`:** iterate all `Interaction` rows joined with their run_model jobs (via `outcomes.job_outcome`). For each run_model call, key by `pipeline` and its param-set (the `arguments_sanitized` minus `pipeline`/`files`, JSON-canonicalized). Build:
- `recommended_defaults[pipeline]` = the param-set most common among **successful** runs, if its support ≥ `MIN_SUPPORT` (2). Empty dict param-sets are allowed (means "defaults worked").
- `warnings` = per pipeline where failures ≥ `MIN_SUPPORT` and failure_rate ≥ 0.5 → `{pipeline, condition, message}` using the most common `error_message` substring.
- `recipes` = top consecutive successful `run_model` pipeline pairs by the same user (ordered by created_at), count ≥ `MIN_SUPPORT` → `{goal, steps}`.
It writes one `ImprovementArtifact(status="proposed", ...)` and returns it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_selfimprove_analyze.py
import uuid
from app import models
from app.database import SessionLocal
from app.selfimprove import analyze


def _user(db):
    u = models.User(username=f"an_{uuid.uuid4().hex[:8]}", password_hash="x")
    db.add(u); db.commit(); db.refresh(u)
    return u


def _job(db, uid, pipeline, status):
    j = models.Job(user_id=uid, title="t", pipeline=pipeline, status=status,
                   error_message=("ProteinMPNN: no PDB content found in payload" if status == "failed" else None))
    db.add(j); db.commit(); db.refresh(j)
    return j


def _interaction(db, uid, pipeline, args, job):
    it = models.Interaction(
        user_id=uid, provider="anthropic", model="m", user_message_len=1, reply_len=1,
        tool_calls=[{"name": "run_model", "arguments_sanitized": {"pipeline": pipeline, **args}, "ok": True}],
        job_ids=[job.id],
    )
    db.add(it); db.commit()


def test_recommended_defaults_from_repeated_success():
    with SessionLocal() as db:
        u = _user(db)
        for _ in range(3):
            j = _job(db, u.id, "esmfold", "completed")
            _interaction(db, u.id, "esmfold", {"num_recycle": 3}, j)
        art = analyze.compute_artifact(db)
        assert art.status == "proposed"
        assert art.payload["recommended_defaults"]["esmfold"] == {"num_recycle": 3}
        assert art.stats["n_interactions"] >= 3


def test_warning_from_repeated_failure():
    with SessionLocal() as db:
        u = _user(db)
        for _ in range(2):
            j = _job(db, u.id, "proteinmpnn", "failed")
            _interaction(db, u.id, "proteinmpnn", {}, j)
        art = analyze.compute_artifact(db)
        warns = art.payload["warnings"]
        assert any(w["pipeline"] == "proteinmpnn" for w in warns)
        assert any("PDB" in w["message"] for w in warns)


def test_no_data_yields_empty_artifact():
    with SessionLocal() as db:
        art = analyze.compute_artifact(db)
        assert art.payload["recommended_defaults"] == {}
        assert art.payload["warnings"] == []
        assert art.payload["recipes"] == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_selfimprove_analyze.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.selfimprove.analyze'`

- [ ] **Step 3: Implement analyze**

Create `app/selfimprove/analyze.py`:

```python
from __future__ import annotations

import json
from collections import Counter, defaultdict

from sqlalchemy.orm import Session

from .. import models
from . import outcomes

MIN_SUPPORT = 2
FAIL_RATE = 0.5


def _param_key(args: dict) -> str:
    clean = {k: v for k, v in (args or {}).items() if k not in ("pipeline", "files")}
    return json.dumps(clean, sort_keys=True, ensure_ascii=False)


def compute_artifact(db: Session) -> models.ImprovementArtifact:
    interactions = db.query(models.Interaction).order_by(models.Interaction.created_at).all()

    # pipeline -> Counter(param_key) among successful runs; and success/fail tallies
    success_params: dict[str, Counter] = defaultdict(Counter)
    fail_counts: dict[str, int] = defaultdict(int)
    total_counts: dict[str, int] = defaultdict(int)
    fail_errors: dict[str, Counter] = defaultdict(Counter)
    # per-user ordered successful pipelines for recipe pairs
    user_seq: dict[int, list[str]] = defaultdict(list)
    n_jobs = 0

    for it in interactions:
        for call in it.tool_calls or []:
            if call.get("name") != "run_model":
                continue
            args = call.get("arguments_sanitized") or {}
            pipeline = args.get("pipeline")
            if not pipeline:
                continue
            jids = it.job_ids or []
            if not jids:
                continue
            out = outcomes.job_outcome(db, jids[0])
            if out["status"] is None:
                continue
            n_jobs += 1
            total_counts[pipeline] += 1
            if out["success"]:
                success_params[pipeline][_param_key(args)] += 1
                user_seq[it.user_id].append(pipeline)
            else:
                fail_counts[pipeline] += 1
                job = db.query(models.Job).filter_by(id=jids[0]).first()
                if job and job.error_message:
                    fail_errors[pipeline][job.error_message] += 1

    recommended: dict[str, dict] = {}
    for pipeline, counter in success_params.items():
        key, support = counter.most_common(1)[0]
        if support >= MIN_SUPPORT:
            recommended[pipeline] = json.loads(key)

    warnings: list[dict] = []
    for pipeline, fails in fail_counts.items():
        total = total_counts[pipeline]
        if fails >= MIN_SUPPORT and total and (fails / total) >= FAIL_RATE:
            msg = fail_errors[pipeline].most_common(1)[0][0] if fail_errors[pipeline] else "high failure rate"
            warnings.append({
                "pipeline": pipeline,
                "condition": f"{fails}/{total} runs failed",
                "message": msg,
            })

    pair_counts: Counter = Counter()
    for seq in user_seq.values():
        for a, b in zip(seq, seq[1:]):
            if a != b:
                pair_counts[(a, b)] += 1
    recipes = [
        {"goal": f"{a} → {b}", "steps": [a, b]}
        for (a, b), c in pair_counts.most_common(5) if c >= MIN_SUPPORT
    ]

    payload = {"recommended_defaults": recommended, "warnings": warnings, "recipes": recipes}
    stats = {"n_interactions": len(interactions), "n_jobs": n_jobs}
    parts = []
    if recommended:
        parts.append(f"{len(recommended)} default(s)")
    if warnings:
        parts.append(f"{len(warnings)} warning(s)")
    if recipes:
        parts.append(f"{len(recipes)} recipe(s)")
    summary = ", ".join(parts) or "no signal yet"

    art = models.ImprovementArtifact(status="proposed", summary=summary, payload=payload, stats=stats)
    db.add(art)
    db.commit()
    db.refresh(art)
    return art
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_selfimprove_analyze.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add app/selfimprove/analyze.py tests/test_selfimprove_analyze.py
git commit -m "feat(selfimprove): compute_artifact — defaults/warnings/recipes from global data"
```

---

### Task 6: Feedback — active artifact + system-prompt injection

**Files:**
- Create: `app/selfimprove/feedback.py`
- Modify: `app/chat/loop.py` (inject active artifact into the system prompt)
- Test: `tests/test_selfimprove_feedback.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_selfimprove_feedback.py
from app import models
from app.database import SessionLocal
from app.selfimprove import feedback


def test_active_artifact_returns_only_active():
    with SessionLocal() as db:
        db.add(models.ImprovementArtifact(status="proposed", summary="p", payload={}, stats={}))
        active = models.ImprovementArtifact(status="active", summary="a",
            payload={"recommended_defaults": {"esmfold": {"num_recycle": 3}},
                     "warnings": [{"pipeline": "proteinmpnn", "condition": "2/2 failed", "message": "no PDB"}],
                     "recipes": []}, stats={})
        db.add(active); db.commit()
        got = feedback.active_artifact(db)
        assert got is not None and got.status == "active"


def test_render_prompt_block_includes_defaults_and_warnings():
    art = models.ImprovementArtifact(status="active", summary="a", payload={
        "recommended_defaults": {"esmfold": {"num_recycle": 3}},
        "warnings": [{"pipeline": "proteinmpnn", "condition": "2/2 failed", "message": "no PDB content"}],
        "recipes": [{"goal": "rfdiffusion → proteinmpnn", "steps": ["rfdiffusion", "proteinmpnn"]}],
    }, stats={})
    block = feedback.render_prompt_block(art)
    assert "esmfold" in block and "num_recycle" in block
    assert "proteinmpnn" in block and "no PDB content" in block
    assert "rfdiffusion → proteinmpnn" in block


def test_render_prompt_block_none_is_empty():
    assert feedback.render_prompt_block(None) == ""
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_selfimprove_feedback.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.selfimprove.feedback'`

- [ ] **Step 3: Implement feedback**

Create `app/selfimprove/feedback.py`:

```python
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from .. import models


def active_artifact(db: Session) -> models.ImprovementArtifact | None:
    return (
        db.query(models.ImprovementArtifact)
        .filter_by(status="active")
        .order_by(models.ImprovementArtifact.created_at.desc())
        .first()
    )


def render_prompt_block(artifact: models.ImprovementArtifact | None) -> str:
    """Compact text injected into the chat system prompt. Empty if no artifact."""
    if artifact is None:
        return ""
    payload = artifact.payload or {}
    lines: list[str] = []
    defaults = payload.get("recommended_defaults") or {}
    if defaults:
        lines.append("Recommended defaults learned from prior successful runs:")
        for pipeline, params in defaults.items():
            lines.append(f"- {pipeline}: {json.dumps(params, ensure_ascii=False)}")
    warnings = payload.get("warnings") or []
    if warnings:
        lines.append("Known failure patterns to avoid or warn the user about:")
        for w in warnings:
            lines.append(f"- {w.get('pipeline')}: {w.get('message')} ({w.get('condition')})")
    recipes = payload.get("recipes") or []
    if recipes:
        lines.append("Common successful multi-step recipes:")
        for r in recipes:
            lines.append(f"- {r.get('goal')}")
    if not lines:
        return ""
    return (
        "\n\n[Learned guidance — aggregate stats from prior runs across the portal; "
        "use as hints, still verify with tools]\n" + "\n".join(lines)
    )
```

- [ ] **Step 4: Inject into the chat loop**

In `app/chat/loop.py`, add the import:

```python
from ..selfimprove import feedback as si_feedback
```

In `run_chat`, build the effective system prompt once before the loop and pass it to `provider.request` instead of the bare `SYSTEM_PROMPT`:

```python
    tools = provider.format_tools(TOOLS)
    messages = provider.build_messages(history)
    system_prompt = SYSTEM_PROMPT + si_feedback.render_prompt_block(si_feedback.active_artifact(db))
    collected: list[dict] = []
    last_text = ""

    for _ in range(max_iters):
        resp = provider.request(api_key, model, system_prompt, messages, tools)
```

(Replace the two existing `provider.request(api_key, model, SYSTEM_PROMPT, messages, tools)` call sites — there is one in the loop — with `system_prompt`.)

- [ ] **Step 5: Run to verify it passes + no regressions**

Run: `.venv/bin/python -m pytest tests/test_selfimprove_feedback.py tests/test_chat.py -q`
Expected: all pass. (The existing chat tests use a FakeProvider whose `request` ignores the system arg, so injection is transparent.)

- [ ] **Step 6: Commit**

```bash
git add app/selfimprove/feedback.py app/chat/loop.py tests/test_selfimprove_feedback.py
git commit -m "feat(selfimprove): inject active artifact into chat system prompt"
```

---

### Task 7: Admin API — list / activate / reject

**Files:**
- Create: `app/routers/selfimprove.py`
- Modify: `app/main.py` (import + include_router)
- Test: `tests/test_selfimprove_api.py`

**Gate:** `current_user.username` must be in `settings.selfimprove_admin_users` (comma-split). Empty allowlist → 403 for everyone. Activating an artifact sets it `active` and demotes any previously-active one to `rejected` (only one active).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_selfimprove_api.py
import uuid
import pytest
from fastapi.testclient import TestClient
from app import models
from app.database import SessionLocal
from app.config import get_settings


@pytest.fixture
def client_and_admin(monkeypatch):
    s = get_settings()
    admin_name = f"admin_{uuid.uuid4().hex[:6]}"
    monkeypatch.setattr(s, "selfimprove_admin_users", admin_name)
    monkeypatch.setattr(s, "kbf_allow_insecure_sso_header", True)
    monkeypatch.setattr(s, "kbf_forward_auth_secret", "")
    monkeypatch.setattr(s, "selfimprove_enabled", False)  # never spawn the scheduler thread in tests
    from app.main import app
    return TestClient(app), admin_name


def _make_proposed(db):
    a = models.ImprovementArtifact(status="proposed", summary="p", payload={}, stats={})
    db.add(a); db.commit(); db.refresh(a)
    return a.id


def test_non_admin_cannot_activate(client_and_admin):
    client, admin = client_and_admin
    with SessionLocal() as db:
        aid = _make_proposed(db)
    # A plain (non-reserved-prefix) username authenticates fine but isn't in the
    # admin allowlist → 403. (A "sso:"-prefixed value would be rejected at auth as
    # a spoof attempt → 401, which is not what this test asserts.)
    r = client.post(f"/api/selfimprove/artifacts/{aid}/activate", headers={"X-KBF-User": "notadmin_user"})
    assert r.status_code == 403


def test_admin_activate_promotes_one(client_and_admin):
    client, admin = client_and_admin
    with SessionLocal() as db:
        aid = _make_proposed(db)
    r = client.post(f"/api/selfimprove/artifacts/{aid}/activate", headers={"X-KBF-User": admin})
    assert r.status_code == 200
    with SessionLocal() as db:
        assert db.query(models.ImprovementArtifact).filter_by(id=aid).first().status == "active"


def test_list_returns_artifacts(client_and_admin):
    client, admin = client_and_admin
    with SessionLocal() as db:
        _make_proposed(db)
    r = client.get("/api/selfimprove/artifacts", headers={"X-KBF-User": admin})
    assert r.status_code == 200
    assert isinstance(r.json()["artifacts"], list) and len(r.json()["artifacts"]) >= 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_selfimprove_api.py -q`
Expected: FAIL — 404s (router not mounted).

- [ ] **Step 3: Implement the router**

Create `app/routers/selfimprove.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models
from ..auth import get_current_user
from ..config import get_settings
from ..database import get_db

router = APIRouter(prefix="/api/selfimprove", tags=["selfimprove"])
settings = get_settings()


def _require_admin(user: models.User) -> None:
    allow = {u.strip() for u in (settings.selfimprove_admin_users or "").split(",") if u.strip()}
    if user.username not in allow:
        raise HTTPException(status_code=403, detail="self-improvement admin only")


def _serialize(a: models.ImprovementArtifact) -> dict:
    return {
        "id": a.id, "status": a.status, "summary": a.summary,
        "payload": a.payload, "stats": a.stats,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


@router.get("/artifacts")
def list_artifacts(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    _require_admin(current_user)
    rows = db.query(models.ImprovementArtifact).order_by(models.ImprovementArtifact.created_at.desc()).all()
    return {"artifacts": [_serialize(a) for a in rows]}


@router.post("/artifacts/{artifact_id}/activate")
def activate(artifact_id: str, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    _require_admin(current_user)
    art = db.query(models.ImprovementArtifact).filter_by(id=artifact_id).first()
    if not art:
        raise HTTPException(status_code=404, detail="artifact not found")
    for other in db.query(models.ImprovementArtifact).filter_by(status="active").all():
        other.status = "rejected"
    art.status = "active"
    db.commit()
    return {"ok": True, "id": art.id, "status": art.status}


@router.post("/artifacts/{artifact_id}/reject")
def reject(artifact_id: str, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    _require_admin(current_user)
    art = db.query(models.ImprovementArtifact).filter_by(id=artifact_id).first()
    if not art:
        raise HTTPException(status_code=404, detail="artifact not found")
    art.status = "rejected"
    db.commit()
    return {"ok": True, "id": art.id, "status": art.status}
```

- [ ] **Step 4: Register the router**

In `app/main.py`: add `selfimprove` to the routers import line:

```python
from .routers import assistant, auth, chat, jobs, mcp_tokens, pipelines, rfdiffusion, selfimprove, users, workflows
```

and add after `app.include_router(chat.router)`:

```python
app.include_router(selfimprove.router)
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_selfimprove_api.py -q`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add app/routers/selfimprove.py app/main.py tests/test_selfimprove_api.py
git commit -m "feat(selfimprove): admin API to list/activate/reject artifacts (allowlist-gated)"
```

---

### Task 8: Scheduler + startup wiring + autoactivate

**Files:**
- Create: `app/selfimprove/scheduler.py`
- Modify: `app/main.py` (start/stop the scheduler)
- Test: `tests/test_selfimprove_scheduler.py`

**Behavior:** `run_cycle(db)` calls `analyze.compute_artifact(db)`, then if `settings.selfimprove_autoactivate` is True promotes the new artifact to active (demoting others). The daemon thread mirrors `WorkflowMonitor`. `start()` is a no-op when `settings.selfimprove_enabled` is False, so tests never spawn it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_selfimprove_scheduler.py
import uuid
from app import models
from app.database import SessionLocal
from app.config import get_settings
from app.selfimprove import scheduler


def _seed_success(db):
    u = models.User(username=f"sch_{uuid.uuid4().hex[:8]}", password_hash="x")
    db.add(u); db.commit(); db.refresh(u)
    for _ in range(2):
        j = models.Job(user_id=u.id, title="t", pipeline="esmfold", status="completed")
        db.add(j); db.commit(); db.refresh(j)
        db.add(models.Interaction(user_id=u.id, provider="anthropic", model="m",
            user_message_len=1, reply_len=1,
            tool_calls=[{"name": "run_model", "arguments_sanitized": {"pipeline": "esmfold"}, "ok": True}],
            job_ids=[j.id]))
    db.commit()


def test_run_cycle_creates_proposed_by_default(monkeypatch):
    monkeypatch.setattr(get_settings(), "selfimprove_autoactivate", False)
    with SessionLocal() as db:
        _seed_success(db)
        art = scheduler.run_cycle(db)
        assert art.status == "proposed"


def test_run_cycle_autoactivates_when_enabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "selfimprove_autoactivate", True)
    with SessionLocal() as db:
        _seed_success(db)
        art = scheduler.run_cycle(db)
        assert art.status == "active"
        actives = db.query(models.ImprovementArtifact).filter_by(status="active").count()
        assert actives == 1


def test_scheduler_start_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "selfimprove_enabled", False)
    s = scheduler.SelfImproveScheduler()
    s.start()
    assert not s.thread.is_alive()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_selfimprove_scheduler.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.selfimprove.scheduler'`

- [ ] **Step 3: Implement the scheduler**

Create `app/selfimprove/scheduler.py`:

```python
from __future__ import annotations

import threading

from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import SessionLocal
from . import analyze

settings = get_settings()


def run_cycle(db: Session) -> "object":
    """Compute one artifact; auto-activate it if configured. Returns the artifact."""
    art = analyze.compute_artifact(db)
    if settings.selfimprove_autoactivate:
        from .. import models
        for other in db.query(models.ImprovementArtifact).filter_by(status="active").all():
            if other.id != art.id:
                other.status = "rejected"
        art.status = "active"
        db.commit()
        db.refresh(art)
    return art


class SelfImproveScheduler:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="selfimprove-scheduler", daemon=True)

    def start(self) -> None:
        if not settings.selfimprove_enabled:
            return
        if not self.thread.is_alive():
            self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                with SessionLocal() as db:
                    run_cycle(db)
            except Exception as exc:  # noqa: BLE001
                print(f"[selfimprove] cycle error: {exc}")
            finally:
                self._stop.wait(settings.selfimprove_interval_s)


selfimprove_scheduler = SelfImproveScheduler()
```

- [ ] **Step 4: Wire into main.py**

In `app/main.py` add the import:

```python
from .selfimprove.scheduler import selfimprove_scheduler
```

In `start_monitor()` add `selfimprove_scheduler.start()`; in `stop_monitor()` add `selfimprove_scheduler.stop()`.

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_selfimprove_scheduler.py -q`
Expected: PASS (3 passed).

- [ ] **Step 6: Full suite (no regressions) + confirm scheduler stays off in tests**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all pass. (`SELFIMPROVE_ENABLED` is True by default, but `start()` is only called from the FastAPI startup event, which tests don't trigger except in `test_selfimprove_api.py`'s TestClient. To keep that test from spawning a real thread, the TestClient fixture there does not enter the app lifespan for startup events unless used as a context manager — `TestClient(app)` without `with` does NOT fire startup events. Verify no `selfimprove-scheduler` thread leaks by ensuring tests pass and do not hang.)

- [ ] **Step 7: Commit**

```bash
git add app/selfimprove/scheduler.py app/main.py tests/test_selfimprove_scheduler.py
git commit -m "feat(selfimprove): interval scheduler + startup wiring + autoactivate"
```

---

### Task 9: Deploy + smoke check

**Files:** none (ops)

- [ ] **Step 1: Full suite green**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 2: Restart backend**

Run: `sudo systemctl restart bmp-backend && sleep 3 && systemctl is-active bmp-backend`
Expected: `active`

- [ ] **Step 3: Smoke — capture writes a row**

Send one authenticated `/api/chat` request (reuse the forward-auth secret pattern from prior sessions: header `X-KBF-User` + `X-KBF-Auth`) asking a trivial no-tool question, then confirm an `interactions` row was written:

Run:
```bash
DB=/opt/bio_model_portal/data/app.db
.venv/bin/python -c "import sqlite3; c=sqlite3.connect('$DB'); print('interactions:', c.execute('select count(*) from interactions').fetchone()[0])"
```
Expected: count ≥ 1 after a chat request.

- [ ] **Step 4: Smoke — compute + activate an artifact manually (optional)**

Run a one-off cycle and confirm an artifact row appears:
```bash
.venv/bin/python -c "from app.database import SessionLocal; from app.selfimprove import scheduler; \
  db=SessionLocal(); a=scheduler.run_cycle(db); print(a.status, a.summary, a.stats)"
```
Expected: prints a `proposed` artifact with stats.

- [ ] **Step 5: Commit any ops notes (none expected)** — skip if nothing changed.

---

## Notes for the executor

- **No frontend work in v1.** The admin review is API-only (`/api/selfimprove/artifacts`). A UI can follow later.
- **Determinism:** never call `selfimprove_scheduler.start()` from test code. Tests exercise `run_cycle`/`compute_artifact` directly.
- **Privacy invariant:** `interactions` must never store raw sequences/PDBs or full user text — only lengths + sanitized (base64-stripped, long-string-truncated) tool arguments. The capture tests enforce this; keep them green.
- **Config env for prod rollout (set later in `.env`, not in code):** `SELFIMPROVE_ADMIN_USERS=<the ops SSO sub or kbfportal>` to enable promotion; leave `SELFIMPROVE_AUTOACTIVATE=false` for the human-approval gate.
