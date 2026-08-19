import uuid

from app import models
from app.database import Base, engine, SessionLocal
from app.workflow import orchestrator


def _seed_run(db):
    user = models.User(username=f"orch_tester_{uuid.uuid4().hex[:8]}", password_hash="x")
    db.add(user); db.commit()
    wf = models.Workflow(name="RAPID", template_key="rapid_v1", owner_id=user.id, dag={})
    db.add(wf); db.commit()
    run = models.WorkflowRun(
        workflow_id=wf.id, owner_id=user.id, status="queued",
        input_summary={"sequence": "ACDEFGHIKL", "msa": ["ACDEFGHIKL", "ACDEFGHIKL"]},
    )
    db.add(run); db.commit()
    return user, run


def test_start_run_creates_first_step(monkeypatch):
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(orchestrator, "_is_worker_step", lambda node: False)
    monkeypatch.setattr(orchestrator, "_run_portal_step", lambda db, run, step, node: ({"done": True}, {}))
    with SessionLocal() as db:
        _user, run = _seed_run(db)
        orchestrator.start_run(db, run)
        assert run.status == "running"
        steps = db.query(models.WorkflowRunStep).filter_by(run_id=run.id).all()
        assert len(steps) == 1
        assert steps[0].step_name == "FASTA/PDB Input"


def test_advance_runs_all_portal_steps_to_completion(monkeypatch):
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(orchestrator, "_is_worker_step", lambda node: False)
    monkeypatch.setattr(orchestrator, "_run_portal_step", lambda db, run, step, node: ({"ok": True}, {"score": 1}))
    with SessionLocal() as db:
        _user, run = _seed_run(db)
        orchestrator.start_run(db, run)
        for _ in range(20):
            if run.status in {"completed", "failed"}:
                break
            orchestrator.advance(db, run)
        assert run.status == "completed"
        assert db.query(models.WorkflowRunStep).filter_by(run_id=run.id).count() == 7
