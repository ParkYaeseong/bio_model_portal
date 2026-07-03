# Portal MCP Server (SP2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the portal's models to external AI (Claude/Codex/Gemini via MCP) through a portal-native MCP JSON-RPC endpoint, authenticated by one-click Personal Access Tokens, reusing the existing gateway + jobs.

**Architecture:** A FastAPI route `POST /mcp` implements MCP JSON-RPC (initialize / tools/list / tools/call) — no MCP SDK, matching how `pipeline-mcp` hand-rolls it. Tool functions in `app/mcp/tools.py` reuse `runpod.PIPELINES` and `workflow/job_bridge.create_step_job`. PATs (`kbfpat_…`) map a bearer token to a portal user; a SSO-gated API + settings UI mint/list/revoke them.

**Tech Stack:** FastAPI + SQLAlchemy (SQLite), Next.js 14, existing RunPod-compat gateway.

Spec: `docs/superpowers/specs/2026-07-03-portal-mcp-server-design.md`

Backend paths relative to `portal/backend/`; run tests with `.venv/bin/python -m pytest tests/ -q` (conftest handles temp DB + per-test reset). Frontend relative to `portal/frontend/`.

Reusable pieces (read first):
- `app/runpod.py`: `PIPELINES` (dict of `PipelineDefinition` with `.key,.label,.description,.supports_sequence,.requires_archive,.input_fields` where each field has `.name,.label,.field_type,.required,.options,.placeholder,.helper,.minimum`).
- `app/workflow/job_bridge.py`: `create_step_job(db, *, user_id, title, pipeline, params, input_files=None, sequence=None) -> models.Job` — creates + submits a portal Job via the gateway.
- `app/models.py`: `User`, `Job` (`.id,.user_id,.pipeline,.status,.error_message,.endpoint_id,.result_dir`), `Artifact` (`.id,.job_id,.file_name,.kind,.mime_type,.size_bytes`).

---

## File Structure

**Create (backend):** `app/mcp/__init__.py`, `app/mcp/pat.py`, `app/mcp/tools.py`, `app/mcp/server.py`, `app/routers/mcp_tokens.py`, tests `tests/test_pat.py`, `tests/test_mcp_tools.py`, `tests/test_mcp_server.py`, `tests/test_mcp_tokens_api.py`.
**Modify (backend):** `app/models.py` (+`PersonalAccessToken`), `app/main.py` (+router +mcp route), `app/routers/__init__.py`.
**Create (frontend):** `src/app/mcp/page.tsx`.
**Modify (frontend):** `src/lib/api.ts`.
**Docs:** `README.md` (AI 연결 section + Caddy `/mcp` bypass block).

---

## Task 1: PersonalAccessToken model + PAT store

**Files:** Modify `app/models.py`; Create `app/mcp/__init__.py`, `app/mcp/pat.py`; Test `tests/test_pat.py`.

- [ ] **Step 1: Write the failing test** — create `tests/test_pat.py`:

```python
import uuid

from app import models
from app.database import SessionLocal
from app.mcp import pat


def _user(db):
    u = models.User(username=f"pat_{uuid.uuid4().hex[:8]}", password_hash="x")
    db.add(u); db.commit(); db.refresh(u)
    return u


def test_create_returns_raw_once_and_resolves():
    with SessionLocal() as db:
        u = _user(db)
        raw, row = pat.create_pat(db, u, "claude")
        assert raw.startswith("kbfpat_") and len(raw) > 20
        assert row.token_hash != raw and row.prefix and row.name == "claude"
        resolved = pat.resolve_pat(db, raw)
        assert resolved is not None and resolved.id == u.id


def test_bad_and_revoked_tokens_do_not_resolve():
    with SessionLocal() as db:
        u = _user(db)
        raw, row = pat.create_pat(db, u, "x")
        assert pat.resolve_pat(db, "kbfpat_nonsense") is None
        pat.revoke_pat(db, u, row.id)
        assert pat.resolve_pat(db, raw) is None
```

- [ ] **Step 2: Run it, verify FAIL**

Run: `cd portal/backend && .venv/bin/python -m pytest tests/test_pat.py -q`
Expected: FAIL (`ModuleNotFoundError: app.mcp`).

- [ ] **Step 3: Add the model** — append to `app/models.py` (after `Artifact`):

```python
class PersonalAccessToken(Base):
    __tablename__ = "personal_access_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)  # for display, e.g. kbfpat_ab
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
```

- [ ] **Step 4: Create the package + store** — create empty `app/mcp/__init__.py`; create `app/mcp/pat.py`:

```python
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime

from sqlalchemy.orm import Session

from .. import models

TOKEN_PREFIX = "kbfpat_"


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_pat(db: Session, user: models.User, name: str) -> tuple[str, models.PersonalAccessToken]:
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = models.PersonalAccessToken(
        user_id=user.id, name=(name or "token")[:80],
        token_hash=_hash(raw), prefix=raw[: len(TOKEN_PREFIX) + 4],
    )
    db.add(row); db.commit(); db.refresh(row)
    return raw, row


def resolve_pat(db: Session, raw: str | None) -> models.User | None:
    if not raw or not raw.startswith(TOKEN_PREFIX):
        return None
    row = (
        db.query(models.PersonalAccessToken)
        .filter_by(token_hash=_hash(raw), revoked_at=None)
        .first()
    )
    if not row:
        return None
    row.last_used_at = datetime.utcnow()
    db.commit()
    return db.query(models.User).filter_by(id=row.user_id).first()


def list_pats(db: Session, user: models.User) -> list[models.PersonalAccessToken]:
    return (
        db.query(models.PersonalAccessToken)
        .filter_by(user_id=user.id, revoked_at=None)
        .order_by(models.PersonalAccessToken.created_at.desc())
        .all()
    )


def revoke_pat(db: Session, user: models.User, token_id: str) -> bool:
    row = db.query(models.PersonalAccessToken).filter_by(id=token_id, user_id=user.id).first()
    if not row or row.revoked_at is not None:
        return False
    row.revoked_at = datetime.utcnow()
    db.commit()
    return True
```

- [ ] **Step 5: Run test, verify PASS.** Run: `.venv/bin/python -m pytest tests/test_pat.py -q` → 2 passed.
- [ ] **Step 6: Commit**

```bash
git add portal/backend/app/models.py portal/backend/app/mcp/__init__.py portal/backend/app/mcp/pat.py portal/backend/tests/test_pat.py
git commit -m "feat(mcp): PersonalAccessToken model + PAT store"
```

---

## Task 2: MCP tool functions

**Files:** Create `app/mcp/tools.py`; Test `tests/test_mcp_tools.py`.

Tool functions take `(db, user, arguments)` and return JSON-serializable dicts. `run_model` reuses `job_bridge.create_step_job`. `files` argument (optional) is a list of `{name, base64}` decoded to temp files.

- [ ] **Step 1: Write the failing test** — `tests/test_mcp_tools.py`:

```python
import uuid

from app import models
from app.database import SessionLocal
from app.mcp import tools


def _user(db):
    u = models.User(username=f"t_{uuid.uuid4().hex[:8]}", password_hash="x")
    db.add(u); db.commit(); db.refresh(u)
    return u


def test_list_models_includes_known_pipelines():
    with SessionLocal() as db:
        u = _user(db)
        out = tools.list_models(db, u, {})
        keys = {m["key"] for m in out["models"]}
        assert {"proteinmpnn", "esmfold", "colabfold"}.issubset(keys)
        af = next(m for m in out["models"] if m["key"] == "alphafold")
        assert any(f["name"] == "model_preset" for f in af["input_fields"])


def test_run_model_unknown_pipeline_errors():
    with SessionLocal() as db:
        u = _user(db)
        r = tools.run_model(db, u, {"pipeline": "nope"})
        assert r["ok"] is False and "unknown" in r["error"].lower()


def test_run_model_creates_job(monkeypatch):
    with SessionLocal() as db:
        u = _user(db)

        def fake_create(db_, *, user_id, title, pipeline, params, input_files=None, sequence=None):
            j = models.Job(user_id=user_id, title=title, pipeline=pipeline, status="submitted")
            db_.add(j); db_.commit(); db_.refresh(j)
            return j

        monkeypatch.setattr(tools.job_bridge, "create_step_job", fake_create)
        r = tools.run_model(db, u, {"pipeline": "esmfold", "sequence": "ACDEFG"})
        assert r["ok"] is True and r["job_id"]
        assert tools.job_status(db, u, {"job_id": r["job_id"]})["status"] == "submitted"


def test_job_status_enforces_ownership():
    with SessionLocal() as db:
        u1, u2 = _user(db), _user(db)
        j = models.Job(user_id=u1.id, title="x", pipeline="esmfold", status="submitted")
        db.add(j); db.commit(); db.refresh(j)
        r = tools.job_status(db, u2, {"job_id": j.id})
        assert r["ok"] is False
```

- [ ] **Step 2: Run it, verify FAIL** (`ModuleNotFoundError: app.mcp.tools`).

- [ ] **Step 3: Implement** — create `app/mcp/tools.py`:

```python
from __future__ import annotations

import base64
import re
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from .. import models
from ..runpod import PIPELINES
from ..workflow import job_bridge


def _field(f) -> dict:
    return {
        "name": f.name, "label": f.label, "field_type": f.field_type,
        "required": bool(f.required), "options": f.options,
        "placeholder": f.placeholder, "helper": f.helper, "minimum": f.minimum,
    }


def list_models(db: Session, user: models.User, arguments: dict) -> dict:
    models_out = [
        {
            "key": p.key, "label": p.label, "description": p.description,
            "supports_sequence": p.supports_sequence, "requires_archive": p.requires_archive,
            "input_fields": [_field(f) for f in p.input_fields],
        }
        for p in PIPELINES.values()
    ]
    return {"ok": True, "models": models_out}


def run_model(db: Session, user: models.User, arguments: dict) -> dict:
    pipeline = str(arguments.get("pipeline") or "")
    if pipeline not in PIPELINES:
        return {"ok": False, "error": f"unknown pipeline '{pipeline}'. valid: {sorted(PIPELINES)}"}
    params = arguments.get("parameters") or {}
    sequence = arguments.get("sequence")
    input_files: list[Path] = []
    tmp = None
    for item in arguments.get("files") or []:
        b64 = item.get("base64")
        if not b64:
            continue
        tmp = tmp or Path(tempfile.mkdtemp(prefix="mcp_upload_"))
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(item.get("name") or "input"))
        dest = tmp / (safe or "input")
        dest.write_bytes(base64.b64decode(b64))
        input_files.append(dest)
    job = job_bridge.create_step_job(
        db, user_id=user.id, title=f"mcp {pipeline}", pipeline=pipeline,
        params=params, input_files=input_files or None, sequence=sequence,
    )
    return {"ok": True, "job_id": job.id, "status": job.status, "endpoint_id": job.endpoint_id}


def _owned_job(db: Session, user: models.User, job_id: str) -> models.Job | None:
    return db.query(models.Job).filter_by(id=str(job_id or ""), user_id=user.id).first()


def job_status(db: Session, user: models.User, arguments: dict) -> dict:
    job = _owned_job(db, user, arguments.get("job_id"))
    if not job:
        return {"ok": False, "error": "job not found"}
    return {"ok": True, "job_id": job.id, "pipeline": job.pipeline,
            "status": job.status, "error_message": job.error_message}


def job_result(db: Session, user: models.User, arguments: dict) -> dict:
    job = _owned_job(db, user, arguments.get("job_id"))
    if not job:
        return {"ok": False, "error": "job not found"}
    artifacts = [
        {"id": a.id, "file_name": a.file_name, "kind": a.kind, "size_bytes": a.size_bytes}
        for a in job.artifacts
    ]
    return {"ok": True, "job_id": job.id, "status": job.status, "artifacts": artifacts,
            "download_hint": "GET /api/jobs/{job_id}/artifacts/{artifact_id}"}


def cancel_job(db: Session, user: models.User, arguments: dict) -> dict:
    job = _owned_job(db, user, arguments.get("job_id"))
    if not job:
        return {"ok": False, "error": "job not found"}
    active = {"pending", "submitted", "running", "queued", "in_queue", "in_progress", "processing"}
    if (job.status or "").lower() in active:
        job.status = "cancelled"
        db.commit()
    return {"ok": True, "job_id": job.id, "status": job.status}


# name -> (callable, description, json input schema)
TOOLS = {
    "list_models": (list_models, "List the portal's models and each model's input fields/params.", {"type": "object", "properties": {}}),
    "run_model": (run_model, "Run a portal model. Use list_models first for valid pipeline keys and params.", {
        "type": "object",
        "properties": {
            "pipeline": {"type": "string"},
            "parameters": {"type": "object"},
            "sequence": {"type": "string"},
            "files": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "base64": {"type": "string"}}}},
        },
        "required": ["pipeline"],
    }),
    "job_status": (job_status, "Get a job's status.", {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]}),
    "job_result": (job_result, "Get a job's artifacts + metrics for explaining results.", {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]}),
    "cancel_job": (cancel_job, "Cancel a running job.", {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]}),
}
```

- [ ] **Step 4: Run tests, verify PASS.** Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py -q` → 4 passed. Then full suite.
- [ ] **Step 5: Commit**

```bash
git add portal/backend/app/mcp/tools.py portal/backend/tests/test_mcp_tools.py
git commit -m "feat(mcp): tool functions (list_models/run_model/job_status/job_result/cancel_job)"
```

---

## Task 3: MCP JSON-RPC endpoint (`POST /mcp`)

**Files:** Create `app/mcp/server.py`; Modify `app/main.py`; Test `tests/test_mcp_server.py`.

- [ ] **Step 1: Write the failing test** — `tests/test_mcp_server.py`:

```python
import uuid

from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app
from app.mcp import pat


def _pat():
    with SessionLocal() as db:
        u = models.User(username=f"m_{uuid.uuid4().hex[:8]}", password_hash="x")
        db.add(u); db.commit(); db.refresh(u)
        raw, _ = pat.create_pat(db, u, "cli")
        return raw


def _rpc(client, raw, method, params=None, _id=1):
    return client.post("/mcp", headers={"Authorization": f"Bearer {raw}"},
                       json={"jsonrpc": "2.0", "id": _id, "method": method, "params": params or {}})


def test_requires_valid_pat():
    client = TestClient(app)
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert r.status_code == 401


def test_initialize_and_tools_list():
    client = TestClient(app)
    raw = _pat()
    assert _rpc(client, raw, "initialize").json()["result"]["serverInfo"]["name"] == "bio-model-portal"
    tools = _rpc(client, raw, "tools/list").json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"list_models", "run_model", "job_status", "job_result", "cancel_job"} == names
    assert all("inputSchema" in t for t in tools)


def test_tools_call_list_models():
    client = TestClient(app)
    raw = _pat()
    r = _rpc(client, raw, "tools/call", {"name": "list_models", "arguments": {}}).json()
    text = r["result"]["content"][0]["text"]
    assert "proteinmpnn" in text


def test_unknown_method_returns_jsonrpc_error():
    client = TestClient(app)
    raw = _pat()
    r = _rpc(client, raw, "no/such").json()
    assert r["error"]["code"] == -32601
```

- [ ] **Step 2: Run it, verify FAIL** (401 route missing / 404).

- [ ] **Step 3: Implement** — create `app/mcp/server.py`:

```python
from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..database import SessionLocal
from . import pat
from .tools import TOOLS

router = APIRouter(tags=["mcp"])
SERVER_INFO = {"name": "bio-model-portal", "version": "1.0"}


def _err(_id, code, message):
    return {"jsonrpc": "2.0", "id": _id, "error": {"code": code, "message": message}}


def _ok(_id, result):
    return {"jsonrpc": "2.0", "id": _id, "result": result}


@router.post("/mcp")
async def mcp_endpoint(request: Request):
    auth = request.headers.get("Authorization", "")
    raw = auth[7:] if auth.lower().startswith("bearer ") else None
    with SessionLocal() as db:
        user = pat.resolve_pat(db, raw)
        if user is None:
            return JSONResponse(status_code=401, content={"error": "invalid or missing PAT"})
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(_err(None, -32700, "parse error"))
        _id = body.get("id")
        method = body.get("method")
        params = body.get("params") or {}

        if method == "initialize":
            return JSONResponse(_ok(_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            }))
        if method == "notifications/initialized":
            return JSONResponse(_ok(_id, {}))
        if method == "tools/list":
            tools = [
                {"name": name, "description": desc, "inputSchema": schema}
                for name, (_fn, desc, schema) in TOOLS.items()
            ]
            return JSONResponse(_ok(_id, {"tools": tools}))
        if method == "tools/call":
            name = params.get("name")
            entry = TOOLS.get(name)
            if not entry:
                return JSONResponse(_ok(_id, {
                    "content": [{"type": "text", "text": f"unknown tool: {name}"}],
                    "isError": True,
                }))
            fn = entry[0]
            try:
                result = fn(db, user, params.get("arguments") or {})
                is_error = isinstance(result, dict) and result.get("ok") is False
            except Exception as exc:  # noqa: BLE001
                result, is_error = {"ok": False, "error": str(exc)}, True
            return JSONResponse(_ok(_id, {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                "isError": is_error,
            }))
        return JSONResponse(_err(_id, -32601, f"method not found: {method}"))
```

- [ ] **Step 4: Wire into `app/main.py`** — add `from .mcp.server import router as mcp_router` and `app.include_router(mcp_router)` (alongside the other `include_router` calls).

- [ ] **Step 5: Run tests, verify PASS.** Run: `.venv/bin/python -m pytest tests/test_mcp_server.py -q` → 4 passed. `import app.main` OK. Full suite.
- [ ] **Step 6: Commit**

```bash
git add portal/backend/app/mcp/server.py portal/backend/app/main.py portal/backend/tests/test_mcp_server.py
git commit -m "feat(mcp): JSON-RPC /mcp endpoint (initialize/tools.list/tools.call) with PAT auth"
```

---

## Task 4: PAT management API (SSO-gated)

**Files:** Create `app/routers/mcp_tokens.py`; Modify `app/routers/__init__.py`, `app/main.py`; Test `tests/test_mcp_tokens_api.py`.

- [ ] **Step 1: Write the failing test** — `tests/test_mcp_tokens_api.py`:

```python
import uuid

from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app
from app.auth import get_current_user


def _user():
    with SessionLocal() as db:
        u = models.User(username=f"tok_{uuid.uuid4().hex[:8]}", password_hash="x")
        db.add(u); db.commit(); db.refresh(u)
        return u


def test_create_list_revoke_token():
    user = _user()
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)
    r = client.post("/api/mcp/tokens", json={"name": "claude"})
    assert r.status_code == 200 and r.json()["token"].startswith("kbfpat_")
    tid = r.json()["id"]
    lst = client.get("/api/mcp/tokens").json()["tokens"]
    assert any(t["id"] == tid and "token" not in t for t in lst)
    assert client.post(f"/api/mcp/tokens/{tid}/revoke").status_code == 200
    assert all(t["id"] != tid for t in client.get("/api/mcp/tokens").json()["tokens"])
    app.dependency_overrides.clear()
```

- [ ] **Step 2: Run it, verify FAIL** (404).

- [ ] **Step 3: Implement** — create `app/routers/mcp_tokens.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models
from ..auth import get_current_user
from ..database import get_db
from ..mcp import pat

router = APIRouter(prefix="/api/mcp/tokens", tags=["mcp-tokens"])


class CreateTokenRequest(BaseModel):
    name: str = "token"


def _row(t: models.PersonalAccessToken) -> dict:
    return {"id": t.id, "name": t.name, "prefix": t.prefix,
            "created_at": t.created_at.isoformat(),
            "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None}


@router.post("")
def create_token(payload: CreateTokenRequest, db: Session = Depends(get_db),
                 user: models.User = Depends(get_current_user)):
    raw, row = pat.create_pat(db, user, payload.name)
    return {**_row(row), "token": raw}  # raw shown once


@router.get("")
def list_tokens(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    return {"tokens": [_row(t) for t in pat.list_pats(db, user)]}


@router.post("/{token_id}/revoke")
def revoke_token(token_id: str, db: Session = Depends(get_db),
                 user: models.User = Depends(get_current_user)):
    if not pat.revoke_pat(db, user, token_id):
        raise HTTPException(status_code=404, detail="Token not found.")
    return {"ok": True}
```

- [ ] **Step 4: Register** — in `app/routers/__init__.py` add `mcp_tokens` to the import line; in `app/main.py` add `mcp_tokens` to `from .routers import ...` and `app.include_router(mcp_tokens.router)`.

- [ ] **Step 5: Run test, verify PASS.** Full suite green.
- [ ] **Step 6: Commit**

```bash
git add portal/backend/app/routers/mcp_tokens.py portal/backend/app/routers/__init__.py portal/backend/app/main.py portal/backend/tests/test_mcp_tokens_api.py
git commit -m "feat(mcp): SSO-gated PAT management API (create/list/revoke)"
```

---

## Task 5: Frontend — "AI 연결 (MCP)" settings page

**Files:** Create `src/app/mcp/page.tsx`; Modify `src/lib/api.ts`. (Optionally re-add a header link to `/mcp` — the workflow link was removed; add a `AI 연결` link the same way.)

- [ ] **Step 1: api.ts fetchers** — append to `src/lib/api.ts`:

```typescript
export interface McpToken {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
}

export const listMcpTokens = (token: string) =>
  apiFetch<{ tokens: McpToken[] }>("/api/mcp/tokens", token);

export const createMcpToken = (token: string, name: string) =>
  apiFetch<McpToken & { token: string }>("/api/mcp/tokens", token, {
    method: "POST", body: JSON.stringify({ name }),
  });

export const revokeMcpToken = (token: string, id: string) =>
  apiFetch<{ ok: boolean }>(`/api/mcp/tokens/${id}/revoke`, token, { method: "POST" });
```

- [ ] **Step 2: Settings page** — create `src/app/mcp/page.tsx`: a `"use client"` component (`const token = ""`, same SSO pattern) that:
  - `useSWR(["mcp-tokens"], () => listMcpTokens(token))` and lists tokens (name, prefix, created, last used) with a `해지` button calling `revokeMcpToken` then `mutate()`.
  - a name input + `토큰 생성` button calling `createMcpToken`; on success show the returned raw `token` **once** in a highlighted box with a copy button and a warning it won't be shown again.
  - a static section showing the MCP endpoint URL (`https://biomodel.k-biofoundrycopilot.duckdns.org/mcp`) and a copy-paste JSON snippet for an MCP client, e.g.:
    ```json
    { "mcpServers": { "bio-model-portal": { "url": "https://biomodel.k-biofoundrycopilot.duckdns.org/mcp", "headers": { "Authorization": "Bearer <YOUR_TOKEN>" } } } }
    ```
  Match existing Tailwind styling (`rounded-2xl border border-slate-200 bg-white p-6`, `bg-brand-500` buttons).

- [ ] **Step 3: Build.** `cd portal/frontend && npm run build` → `✓ Compiled successfully`, route `/mcp` listed.
- [ ] **Step 4: Commit**

```bash
git add portal/frontend/src/app/mcp portal/frontend/src/lib/api.ts
git commit -m "feat(mcp-ui): AI 연결(MCP) token settings page"
```

---

## Task 6: Deploy note, Caddy bypass, README, live smoke test

**Files:** Modify `README.md`.

- [ ] **Step 1: README** — add an "AI 연결 (MCP)" section: what it is, the tool list, how a user generates a token and connects Claude/Codex/Gemini (the JSON snippet), and the security model (PAT hashed, per-user job ownership). Include the **required Caddy block** for the infra host:

```
# /mcp must accept a Bearer PAT (no SSO cookie): bypass forward_auth for it.
@mcp path /mcp
handle @mcp {
    reverse_proxy 127.0.0.1:18121   # bmp-backend
}
# (place BEFORE the forward_auth block for biomodel.…; /api/mcp/tokens stays SSO-gated)
```

- [ ] **Step 2: Deploy** — `cd portal/frontend && npm run build`; `sudo systemctl restart bmp-backend bmp-frontend`. Confirm `import app.main` OK and `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18121/mcp` returns 401 (no PAT). Coordinate the Caddy block with the infra host so external `/mcp` works with a Bearer token.

- [ ] **Step 3: Live smoke test** — via the UI generate a token; then from a shell:
  ```bash
  curl -s http://127.0.0.1:18121/mcp -H "Authorization: Bearer <token>" \
    -H 'Content-Type: application/json' \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_models","arguments":{}}}' | head -c 400
  ```
  Expect a JSON-RPC result whose `content[0].text` lists pipelines. Optionally `run_model` a fast model (esmfold) and `job_status` until it completes.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(mcp): AI 연결 (MCP) usage + Caddy /mcp bypass"
```

---

## Self-Review notes (addressed)

- **Spec coverage:** PAT model/store (T1), tools incl. generic run_model reusing job_bridge + list_models from PIPELINES (T2), JSON-RPC endpoint with PAT auth + initialize/tools.list/tools.call + unknown-method error (T3), SSO-gated token API (T4), settings UI + endpoint/snippet (T5), Caddy `/mcp` bypass + README + smoke test (T6). Ownership enforced in every tool (T2). SP3/OAuth/SP4 explicitly deferred.
- **Placeholders:** none; every code step is complete.
- **Type consistency:** `create_pat(db, user, name) -> (raw, row)`, `resolve_pat(db, raw) -> User|None`, `list_pats(db, user)`, `revoke_pat(db, user, id) -> bool`, `TOOLS` mapping `name -> (fn, desc, schema)`, tool fns `(db, user, arguments) -> dict` — used identically across T2/T3/T4.
