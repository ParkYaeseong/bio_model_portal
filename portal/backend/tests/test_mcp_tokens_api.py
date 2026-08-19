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
