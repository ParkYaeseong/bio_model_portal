import uuid

from app import models
from app.database import Base, engine, SessionLocal
from app.workflow import orchestrator


def test_worker_step_submits_then_completes(monkeypatch):
    Base.metadata.create_all(bind=engine)
    created = {}

    def fake_submit(db, run, step, node):
        job = models.Job(user_id=run.owner_id, title=step.step_name, pipeline="mmseqs", status="submitted")
        db.add(job); db.commit(); db.refresh(job)
        step.job_id = job.id
        created["job"] = job
        db.commit()

    def fake_collect(job):
        return {"msa": ["ACDE", "ACDE"]}, {"n_seqs": 2}

    monkeypatch.setattr(orchestrator, "_submit_worker_step", fake_submit)
    monkeypatch.setattr(orchestrator, "_collect_worker_output", fake_collect)
    monkeypatch.setattr(orchestrator, "_run_portal_step", lambda db, run, step, node: ({}, {}))

    with SessionLocal() as db:
        user = models.User(username=f"wstep_{uuid.uuid4().hex[:8]}", password_hash="x"); db.add(user); db.commit()
        wf = models.Workflow(name="RAPID", template_key="rapid_v1", owner_id=user.id, dag={}); db.add(wf); db.commit()
        run = models.WorkflowRun(workflow_id=wf.id, owner_id=user.id, status="queued", input_summary={"sequence": "ACDE"})
        db.add(run); db.commit()
        orchestrator.start_run(db, run)  # spawns Input(portal) step 0, running
        orchestrator.advance(db, run)    # completes Input, spawns MSA(worker) step 1
        orchestrator.advance(db, run)    # MSA has no job_id -> fake_submit sets it
        msa_step = db.query(models.WorkflowRunStep).filter_by(run_id=run.id, step_name="MSA Search").first()
        assert msa_step.status == "running" and msa_step.job_id
        created["job"].status = "completed"; db.commit()
        orchestrator.advance(db, run)    # job done -> fake_collect -> complete MSA
        db.refresh(msa_step)
        assert msa_step.status == "completed"
        assert msa_step.metrics == {"n_seqs": 2}
