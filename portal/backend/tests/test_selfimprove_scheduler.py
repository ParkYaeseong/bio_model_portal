import uuid
from app import models
from app.database import SessionLocal
from app.config import get_settings
from app.selfimprove import scheduler


def _seed_success(db):
    u = models.User(username=f"sch_{uuid.uuid4().hex[:8]}", password_hash="x")
    db.add(u); db.commit(); db.refresh(u)
    for _ in range(2):
        j = models.Job(user_id=u.id, title="t", pipeline="esmfold", status="completed")
        db.add(j); db.commit(); db.refresh(j)
        db.add(models.Interaction(user_id=u.id, provider="anthropic", model="m",
            user_message_len=1, reply_len=1,
            tool_calls=[{"name": "run_model", "arguments_sanitized": {"pipeline": "esmfold"}, "ok": True}],
            job_ids=[j.id]))
    db.commit()


def test_run_cycle_creates_proposed_by_default(monkeypatch):
    monkeypatch.setattr(get_settings(), "selfimprove_autoactivate", False)
    with SessionLocal() as db:
        _seed_success(db)
        art = scheduler.run_cycle(db)
        assert art.status == "proposed"


def test_run_cycle_autoactivates_when_enabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "selfimprove_autoactivate", True)
    with SessionLocal() as db:
        _seed_success(db)
        art = scheduler.run_cycle(db)
        assert art.status == "active"
        actives = db.query(models.ImprovementArtifact).filter_by(status="active").count()
        assert actives == 1


def test_scheduler_start_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "selfimprove_enabled", False)
    s = scheduler.SelfImproveScheduler()
    s.start()
    assert not s.thread.is_alive()
