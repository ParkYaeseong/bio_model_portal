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
