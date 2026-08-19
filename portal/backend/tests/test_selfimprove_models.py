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
