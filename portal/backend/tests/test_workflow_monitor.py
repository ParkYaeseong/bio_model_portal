import uuid

from app import models
from app.database import Base, engine, SessionLocal
from app.workflow import monitor, orchestrator


def test_monitor_tick_advances_running_runs(monkeypatch):
    Base.metadata.create_all(bind=engine)
    calls = {"n": 0}
    monkeypatch.setattr(orchestrator, "advance", lambda db, run: calls.__setitem__("n", calls["n"] + 1))
    with SessionLocal() as db:
        user = models.User(username=f"mon_{uuid.uuid4().hex[:8]}", password_hash="x"); db.add(user); db.commit()
        wf = models.Workflow(name="RAPID", template_key="rapid_v1", owner_id=user.id, dag={}); db.add(wf); db.commit()
        db.add(models.WorkflowRun(workflow_id=wf.id, owner_id=user.id, status="running")); db.commit()
    monitor.tick_once()
    assert calls["n"] == 1
