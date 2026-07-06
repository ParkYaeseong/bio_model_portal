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
    # We send this raw value as X-KBF-User; the backend provisions the account
    # with username "sso:<value>", so the admin allowlist must hold that form.
    admin_sub = f"admin_{uuid.uuid4().hex[:6]}"
    monkeypatch.setattr(s, "selfimprove_admin_users", f"sso:{admin_sub}")
    monkeypatch.setattr(s, "kbf_allow_insecure_sso_header", True)
    monkeypatch.setattr(s, "kbf_forward_auth_secret", "")
    monkeypatch.setattr(s, "selfimprove_enabled", False)  # never spawn the scheduler thread in tests
    from app.main import app
    return TestClient(app), admin_sub


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
