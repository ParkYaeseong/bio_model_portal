import uuid

from fastapi.testclient import TestClient

from app import chaining, models
from app.database import SessionLocal
from app.main import app
from app.auth import get_current_user


def _user():
    with SessionLocal() as db:
        u = models.User(username=f"chains_{uuid.uuid4().hex[:8]}", password_hash="x")
        db.add(u); db.commit(); db.refresh(u)
        return u


def test_compat_endpoint_returns_graph():
    user = _user()
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)
    r = client.get("/api/chains/compat")
    assert r.status_code == 200
    payload = r.json()
    assert payload["nodes"] and payload["edges"] and payload["examples"]
    assert payload == chaining.compat_graph()
    app.dependency_overrides.clear()
