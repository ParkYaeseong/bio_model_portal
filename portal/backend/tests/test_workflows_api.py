import io
import uuid

from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app
from app.auth import get_current_user


def _make_user():
    with SessionLocal() as db:
        u = models.User(username=f"apiuser_{uuid.uuid4().hex[:8]}", password_hash="x")
        db.add(u); db.commit(); db.refresh(u)
        return u


def test_instantiate_list_and_upload():
    user = _make_user()
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)

    r = client.post("/api/workflows", json={"template_key": "rapid_v1", "name": "my rapid"})
    assert r.status_code == 200, r.text
    wf_id = r.json()["id"]

    r = client.get("/api/workflows")
    assert r.status_code == 200
    assert any(w["id"] == wf_id for w in r.json()["workflows"])

    r = client.get(f"/api/workflows/{wf_id}")
    assert r.status_code == 200 and r.json()["template_key"] == "rapid_v1"

    r = client.post("/api/workflows/upload", files={"file": ("bb.pdb", io.BytesIO(b"ATOM  ...\n"), "chemical/x-pdb")})
    assert r.status_code == 200, r.text
    assert r.json()["backbone_path"].endswith("bb.pdb")

    r = client.post("/api/workflows", json={"template_key": "nope"})
    assert r.status_code == 404

    app.dependency_overrides.clear()


def test_upload_sanitizes_traversal_filename_and_run_rejects_foreign_path(tmp_path):
    user = _make_user()
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)

    # 1) crafted traversal filename must be sanitized to a safe basename inside the upload dir
    r = client.post("/api/workflows/upload",
                    files={"file": ("../../../etc/evil.pdb", io.BytesIO(b"ATOM\n"), "chemical/x-pdb")})
    assert r.status_code == 200, r.text
    bp = r.json()["backbone_path"]
    assert "/uploads/" in bp and ".." not in bp and bp.endswith(".._.._.._etc_evil.pdb") is False
    assert r.json()["file_name"].endswith("evil.pdb") and "/" not in r.json()["file_name"]

    wf = client.post("/api/workflows", json={"template_key": "rapid_v1"}).json()

    # 2) a backbone_path outside the user's uploads tree is rejected
    outside = tmp_path / "secret.pdb"
    outside.write_text("ATOM\n")
    r = client.post(f"/api/workflows/{wf['id']}/runs", json={"backbone_path": str(outside)})
    assert r.status_code == 400, r.text

    # 3) the legitimately-uploaded path is accepted
    r = client.post(f"/api/workflows/{wf['id']}/runs", json={"backbone_path": bp})
    assert r.status_code == 200, r.text

    app.dependency_overrides.clear()
