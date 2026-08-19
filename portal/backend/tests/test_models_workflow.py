from app import models
from app.database import Base, engine, SessionLocal


def test_workflow_models_create_and_cascade():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        user = models.User(username="wf_tester", password_hash="x")
        db.add(user)
        db.commit()
        wf = models.Workflow(name="RAPID", template_key="rapid_v1", owner_id=user.id, dag={"nodes": []})
        db.add(wf)
        db.commit()
        run = models.WorkflowRun(workflow_id=wf.id, owner_id=user.id, status="queued")
        db.add(run)
        db.commit()
        step = models.WorkflowRunStep(run_id=run.id, order=0, step_name="MSA Search", worker_name="gateway.mmseqs", status="queued")
        db.add(step)
        db.commit()
        assert step.id and run.id and wf.id
        db.delete(run)
        db.commit()
        assert db.query(models.WorkflowRunStep).filter_by(id=step.id).first() is None
