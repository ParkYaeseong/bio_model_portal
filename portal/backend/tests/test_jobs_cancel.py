from datetime import datetime

from app import models
from app.database import Base, engine, SessionLocal
from app.routers import jobs as J


class _FakeClient:
    calls: list = []

    def cancel(self, ep, jid):
        _FakeClient.calls.append((ep, jid))
        return {"id": jid, "status": "FAILED"}


def _job(db, user_id, status):
    job = models.Job(
        title="t", pipeline="alphafold", status=status,
        endpoint_id="ep1", runpod_job_id="gw-1", user_id=user_id,
        created_at=datetime(2026, 1, 1), updated_at=datetime(2026, 1, 1),
    )
    db.add(job); db.commit(); db.refresh(job)
    return job


def test_cancel_active_job_calls_client_and_marks_cancelled(monkeypatch):
    Base.metadata.create_all(bind=engine)
    _FakeClient.calls = []
    monkeypatch.setattr(J, "RunpodClient", lambda: _FakeClient())
    with SessionLocal() as db:
        u = models.User(username="cancel_user1", password_hash="x"); db.add(u); db.commit()
        job = _job(db, u.id, "in_progress")
        assert J._cancel_active_job(job) is True
        assert job.status == "cancelled"
        assert _FakeClient.calls == [("ep1", "gw-1")]  # remote compute actually cancelled


def test_cancel_terminal_job_is_noop(monkeypatch):
    Base.metadata.create_all(bind=engine)
    _FakeClient.calls = []
    monkeypatch.setattr(J, "RunpodClient", lambda: _FakeClient())
    with SessionLocal() as db:
        u = models.User(username="cancel_user2", password_hash="x"); db.add(u); db.commit()
        job = _job(db, u.id, "completed")
        assert J._cancel_active_job(job) is False
        assert job.status == "completed"
        assert _FakeClient.calls == []


def test_cancel_survives_remote_failure(monkeypatch):
    Base.metadata.create_all(bind=engine)

    class _Boom:
        def cancel(self, ep, jid):
            raise RuntimeError("gateway down")

    monkeypatch.setattr(J, "RunpodClient", lambda: _Boom())
    with SessionLocal() as db:
        u = models.User(username="cancel_user3", password_hash="x"); db.add(u); db.commit()
        job = _job(db, u.id, "in_queue")
        # remote cancel throws, but the local job must still be marked cancelled
        assert J._cancel_active_job(job) is True
        assert job.status == "cancelled"


def test_cancel_endpoint_returns_cancelled(monkeypatch):
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(J, "RunpodClient", lambda: _FakeClient())
    with SessionLocal() as db:
        u = models.User(username="cancel_user4", password_hash="x"); db.add(u); db.commit()
        job = _job(db, u.id, "in_progress")
        result = J.cancel_job(job_id=job.id, db=db, current_user=u)
        assert result.status == "cancelled"
