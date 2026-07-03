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
