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


def test_no_signal_creates_nothing():
    with SessionLocal() as db:
        art = analyze.compute_artifact(db)
        assert art is None
        assert db.query(models.ImprovementArtifact).count() == 0


def test_duplicate_payload_not_recreated():
    with SessionLocal() as db:
        u = _user(db)
        for _ in range(3):
            j = _job(db, u.id, "esmfold", "completed")
            _interaction(db, u.id, "esmfold", {"num_recycle": 3}, j)
        first = analyze.compute_artifact(db)
        assert first is not None
        # Same underlying data → identical payload → no duplicate row.
        second = analyze.compute_artifact(db)
        assert second is None
        assert db.query(models.ImprovementArtifact).count() == 1
