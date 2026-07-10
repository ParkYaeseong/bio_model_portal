import uuid
from pathlib import Path

from app import models
from app.database import SessionLocal
from app.tasks import monitor


def _user(db):
    u = models.User(username=f"t_{uuid.uuid4().hex[:8]}", password_hash="x")
    db.add(u); db.commit(); db.refresh(u)
    return u


def _completed_with_structure(db, user, tmp_path, pipeline="rfdiffusion"):
    j = models.Job(user_id=user.id, title="src", pipeline=pipeline, status="completed")
    db.add(j); db.commit(); db.refresh(j)
    pdb = tmp_path / "backbone.pdb"; pdb.write_text("ATOM\n")
    db.add(models.Artifact(job_id=j.id, file_name="backbone.pdb", file_path=str(pdb), kind="structure"))
    db.commit(); db.refresh(j)
    return j


def _capture(monkeypatch, captured):
    from app import chaining_exec
    def fake_create(db_, *, user_id, title, pipeline, params, input_files=None, sequence=None):
        captured.setdefault("jobs", []).append(
            {"pipeline": pipeline, "files": [Path(p).name for p in (input_files or [])]})
        j = models.Job(user_id=user_id, title=title, pipeline=pipeline, status="submitted")
        db_.add(j); db_.commit(); db_.refresh(j)
        return j
    monkeypatch.setattr(chaining_exec.job_bridge, "create_step_job", fake_create)


def test_advance_submits_next_step_on_completion(monkeypatch, tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        src = _completed_with_structure(db, u, tmp_path)
        db.add(models.PendingChain(user_id=u.id, source_job_id=src.id,
                                   steps=[{"pipeline": "proteinmpnn", "parameters": {}}], status="pending"))
        db.commit()
        captured = {}; _capture(monkeypatch, captured)
        monitor._advance_pending_chains(db, src, ok=True); db.commit()
        assert captured["jobs"][0]["pipeline"] == "proteinmpnn"
        assert "backbone.pdb" in captured["jobs"][0]["files"]
        pc = db.query(models.PendingChain).filter_by(source_job_id=src.id).one()
        assert pc.status == "done"


def test_advance_repoints_multi_step_chain(monkeypatch, tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        src = _completed_with_structure(db, u, tmp_path)
        db.add(models.PendingChain(user_id=u.id, source_job_id=src.id, status="pending",
            steps=[{"pipeline": "proteinmpnn", "parameters": {}},
                   {"pipeline": "colabfold", "parameters": {}}]))
        db.commit()
        captured = {}; _capture(monkeypatch, captured)
        monitor._advance_pending_chains(db, src, ok=True); db.commit()
        pc = db.query(models.PendingChain).filter_by(user_id=u.id).one()
        assert pc.status == "pending"
        assert pc.steps == [{"pipeline": "colabfold", "parameters": {}}]
        assert pc.source_job_id != src.id


def test_advance_cancels_on_failure(monkeypatch, tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        src = _completed_with_structure(db, u, tmp_path)
        src.status = "failed"; db.commit()
        db.add(models.PendingChain(user_id=u.id, source_job_id=src.id,
                                   steps=[{"pipeline": "proteinmpnn"}], status="pending"))
        db.commit()
        monitor._advance_pending_chains(db, src, ok=False); db.commit()
        assert db.query(models.PendingChain).filter_by(source_job_id=src.id).one().status == "cancelled"


def test_advance_is_idempotent(monkeypatch, tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        src = _completed_with_structure(db, u, tmp_path)
        db.add(models.PendingChain(user_id=u.id, source_job_id=src.id,
                                   steps=[{"pipeline": "proteinmpnn"}], status="pending"))
        db.commit()
        captured = {}; _capture(monkeypatch, captured)
        monitor._advance_pending_chains(db, src, ok=True); db.commit()
        monitor._advance_pending_chains(db, src, ok=True); db.commit()
        assert len(captured.get("jobs", [])) == 1


def test_advance_sees_unflushed_artifacts(monkeypatch, tmp_path):
    # Mirrors _update_job: _persist_output adds artifacts WITHOUT flushing, then the
    # hook runs in the same transaction. plan_chain must still see them (db.flush()).
    with SessionLocal() as db:
        u = _user(db)
        j = models.Job(user_id=u.id, title="src", pipeline="rfdiffusion", status="completed")
        db.add(j); db.commit(); db.refresh(j)
        db.add(models.PendingChain(user_id=u.id, source_job_id=j.id,
               steps=[{"pipeline": "proteinmpnn", "parameters": {}}], status="pending"))
        db.commit()
        # artifact added AFTER the pending-chain commit and left UNFLUSHED, like _persist_output
        pdb = tmp_path / "backbone.pdb"; pdb.write_text("ATOM\n")
        db.add(models.Artifact(job_id=j.id, file_name="backbone.pdb", file_path=str(pdb), kind="structure"))
        captured = {}; _capture(monkeypatch, captured)
        monitor._advance_pending_chains(db, j, ok=True); db.commit()
        assert captured.get("jobs"), "hook did not submit the next step (artifacts invisible?)"
        assert "backbone.pdb" in captured["jobs"][0]["files"]
        assert db.query(models.PendingChain).filter_by(source_job_id=j.id).one().status == "done"
