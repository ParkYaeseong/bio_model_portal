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
