# tests/test_selfimprove_outcomes.py
import json, uuid
from pathlib import Path
from app import models
from app.database import SessionLocal
from app.selfimprove import outcomes


def _user(db):
    u = models.User(username=f"out_{uuid.uuid4().hex[:8]}", password_hash="x")
    db.add(u); db.commit(); db.refresh(u)
    return u


def test_job_outcome_success_and_missing():
    with SessionLocal() as db:
        u = _user(db)
        j = models.Job(user_id=u.id, title="t", pipeline="esmfold", status="completed")
        db.add(j); db.commit(); db.refresh(j)
        out = outcomes.job_outcome(db, j.id)
        assert out["status"] == "completed" and out["success"] is True
        missing = outcomes.job_outcome(db, "nope")
        assert missing["status"] is None and missing["success"] is False


def test_job_metrics_reads_plddt_from_output_json(tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        d = tmp_path / "res"; d.mkdir()
        (d / "output.json").write_text(json.dumps({"plddt": 88.5, "iptm": 0.7}))
        j = models.Job(user_id=u.id, title="t", pipeline="colabfold", status="completed", result_dir=str(d))
        db.add(j); db.commit(); db.refresh(j)
        m = outcomes.job_metrics(j)
        assert round(m["plddt"], 1) == 88.5 and round(m["iptm"], 1) == 0.7
