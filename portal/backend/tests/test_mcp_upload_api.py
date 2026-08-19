"""HTTP upload channel for MCP clients that are not on the portal host.

A laptop client cannot pass a server path, and base64 through a tool call costs
the model ~75k tokens for a 227 KB PDB — so it uploads once over plain HTTP with
the same PAT and then references the file_id.
"""
import hashlib
import shutil
import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app
from app.mcp import files as mcp_files
from app.mcp import pat


def _pat_and_user():
    """(raw PAT, detached stand-in carrying the user id).

    The whole test session shares one STORAGE_ROOT while the DB is wiped per
    test, so user ids repeat and workspaces would carry over between tests —
    start each one from an empty workspace.
    """
    with SessionLocal() as db:
        u = models.User(username=f"u_{uuid.uuid4().hex[:8]}", password_hash="x")
        db.add(u); db.commit(); db.refresh(u)
        raw, _ = pat.create_pat(db, u, "laptop")
        user = SimpleNamespace(id=u.id)
    shutil.rmtree(mcp_files.workspace_dir(user), ignore_errors=True)
    return raw, user


def _auth(raw):
    return {"Authorization": f"Bearer {raw}"}


def test_upload_requires_a_valid_pat():
    client = TestClient(app)
    r = client.post("/mcp/files", files={"files": ("x.pdb", b"ATOM\n")})
    assert r.status_code == 401
    r = client.post("/mcp/files", headers=_auth("kbfpat_nope"),
                    files={"files": ("x.pdb", b"ATOM\n")})
    assert r.status_code == 401


def test_multipart_upload_returns_a_verifiable_handle():
    raw, user = _pat_and_user()
    client = TestClient(app)
    body = b"ATOM      1  N   ALA A   1\n" * 500
    r = client.post("/mcp/files", headers=_auth(raw), files={"files": ("her2.pdb", body)})
    assert r.status_code == 200
    payload = r.json()
    assert payload["ok"] is True
    entry = payload["files"][0]
    assert entry["file_id"] == "her2.pdb"
    assert entry["size_bytes"] == len(body)
    assert entry["sha256"] == hashlib.sha256(body).hexdigest()
    assert (mcp_files.workspace_dir(user) / "her2.pdb").read_bytes() == body


def test_uploaded_file_is_usable_as_a_run_input(monkeypatch, tmp_path):
    from app.mcp import tools

    raw, user = _pat_and_user()
    client = TestClient(app)
    client.post("/mcp/files", headers=_auth(raw), files={"files": ("ab.pdb", b"ATOM\n")})

    captured = {}

    def fake_create(db_, *, user_id, title, pipeline, params, input_files=None, sequence=None):
        captured["input_files"] = [p.name for p in (input_files or [])]
        job = models.Job(user_id=user_id, title=title, pipeline=pipeline, status="submitted")
        db_.add(job); db_.commit(); db_.refresh(job)
        return job

    monkeypatch.setattr(tools.job_bridge, "create_step_job", fake_create)
    r = client.post("/mcp", headers=_auth(raw), json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "run_model", "arguments": {
            "pipeline": "antifold", "files": [{"file_id": "ab.pdb"}]}},
    })
    assert r.json()["result"]["isError"] is False
    assert captured["input_files"] == ["ab.pdb"]


def test_raw_body_upload_needs_a_name_and_then_works():
    raw, _user = _pat_and_user()
    client = TestClient(app)
    r = client.post("/mcp/files", headers=_auth(raw), content=b"ATOM\n")
    assert r.status_code == 400 and "name" in r.json()["error"]
    r = client.post("/mcp/files?name=raw.pdb", headers=_auth(raw), content=b"ATOM\n")
    assert r.status_code == 200 and r.json()["files"][0]["file_id"] == "raw.pdb"


def test_append_resumes_an_interrupted_upload():
    raw, _user = _pat_and_user()
    client = TestClient(app)
    client.post("/mcp/files?name=part.pdb", headers=_auth(raw), content=b"HEAD")
    r = client.post("/mcp/files?name=part.pdb&append=true", headers=_auth(raw), content=b"TAIL")
    assert r.json()["files"][0]["size_bytes"] == 8
    assert r.json()["files"][0]["sha256"] == hashlib.sha256(b"HEADTAIL").hexdigest()


def test_several_files_upload_in_one_request():
    raw, _user = _pat_and_user()
    client = TestClient(app)
    r = client.post("/mcp/files", headers=_auth(raw), files=[
        ("files", ("a.pdb", b"ATOM A\n")),
        ("files", ("b.sdf", b"MOL B\n")),
    ])
    assert [f["file_id"] for f in r.json()["files"]] == ["a.pdb", "b.sdf"]


def test_oversize_upload_is_refused_and_leaves_no_partial_file(monkeypatch):
    raw, user = _pat_and_user()
    monkeypatch.setattr(mcp_files, "MAX_FILE_BYTES", 16)
    client = TestClient(app)
    r = client.post("/mcp/files", headers=_auth(raw), files={"files": ("big.pdb", b"X" * 64)})
    assert r.status_code == 400 and "limit" in r.json()["error"]
    assert not (mcp_files.workspace_dir(user) / "big.pdb").exists()


def test_empty_upload_is_refused():
    raw, _user = _pat_and_user()
    client = TestClient(app)
    r = client.post("/mcp/files", headers=_auth(raw), files={"files": ("empty.pdb", b"")})
    assert r.status_code == 400 and "no file content" in r.json()["error"]


def test_listing_is_scoped_to_the_token_owner():
    raw_a, _ = _pat_and_user()
    raw_b, _ = _pat_and_user()
    client = TestClient(app)
    client.post("/mcp/files", headers=_auth(raw_a), files={"files": ("mine.pdb", b"ATOM\n")})
    assert [f["file_id"] for f in client.get("/mcp/files", headers=_auth(raw_a)).json()["files"]] == ["mine.pdb"]
    assert client.get("/mcp/files", headers=_auth(raw_b)).json()["files"] == []
    assert client.get("/mcp/files").status_code == 401


def test_upload_cannot_escape_the_workspace_with_a_crafted_name():
    raw, user = _pat_and_user()
    client = TestClient(app)
    r = client.post("/mcp/files", headers=_auth(raw),
                    files={"files": ("../../etc/passwd", b"ATOM\n")})
    assert r.status_code == 200
    stored = r.json()["files"][0]["file_id"]
    assert "/" not in stored and ".." not in stored
    assert (mcp_files.workspace_dir(user) / stored).exists()
