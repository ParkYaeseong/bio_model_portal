from datetime import datetime, timedelta

from app import models
from app.database import Base, engine, SessionLocal
from app import queue_estimate as qe


# --- pure math (no DB) -------------------------------------------------------

def test_terminal_job_has_no_estimate():
    assert qe.build_estimate(status="completed", elapsed_seconds=10, ahead=0, avg_seconds=100) is None
    assert qe.build_estimate(status="failed", elapsed_seconds=10, ahead=2, avg_seconds=100) is None


def test_running_eta_is_avg_minus_elapsed():
    est = qe.build_estimate(status="in_progress", elapsed_seconds=120, ahead=0, avg_seconds=300)
    assert est["queue_position"] == 0
    assert est["elapsed_seconds"] == 120
    assert est["avg_seconds"] == 300
    assert est["eta_seconds"] == 180


def test_running_past_average_clamps_to_zero():
    est = qe.build_estimate(status="running", elapsed_seconds=400, ahead=0, avg_seconds=300)
    assert est["eta_seconds"] == 0


def test_queued_eta_counts_jobs_ahead():
    est = qe.build_estimate(status="in_queue", elapsed_seconds=5, ahead=3, avg_seconds=600)
    assert est["queue_position"] == 3
    # (3 ahead + itself) * 600
    assert est["eta_seconds"] == 2400


def test_missing_history_omits_eta_but_keeps_position():
    est = qe.build_estimate(status="in_queue", elapsed_seconds=5, ahead=2, avg_seconds=None)
    assert est["queue_position"] == 2
    assert "eta_seconds" not in est
    assert "avg_seconds" not in est


# --- DB-backed helpers (isolated via a unique endpoint/pipeline) -------------

def _mk_job(db, user_id, *, pipeline, endpoint, status, created, updated=None):
    job = models.Job(
        title="t", pipeline=pipeline, status=status,
        endpoint_id=endpoint, user_id=user_id,
        created_at=created, updated_at=updated or created,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def test_queue_ahead_counts_earlier_active_same_endpoint():
    Base.metadata.create_all(bind=engine)
    ep = "test-ep-qahead"
    with SessionLocal() as db:
        u = models.User(username="qe_ahead_user", password_hash="x")
        db.add(u); db.commit()
        base = datetime(2026, 1, 1, 0, 0, 0)
        # two active jobs ahead (earlier), one completed (ignored), one later (ignored)
        _mk_job(db, u.id, pipeline="colabfold", endpoint=ep, status="in_queue", created=base)
        _mk_job(db, u.id, pipeline="colabfold", endpoint=ep, status="in_progress", created=base + timedelta(minutes=1))
        _mk_job(db, u.id, pipeline="colabfold", endpoint=ep, status="completed", created=base + timedelta(minutes=2))
        mine = _mk_job(db, u.id, pipeline="colabfold", endpoint=ep, status="in_queue", created=base + timedelta(minutes=3))
        _mk_job(db, u.id, pipeline="colabfold", endpoint=ep, status="in_queue", created=base + timedelta(minutes=4))
        # different endpoint should not count
        _mk_job(db, u.id, pipeline="colabfold", endpoint="test-ep-other", status="in_queue", created=base)
        assert qe.queue_ahead(db, mine) == 2


def test_average_durations_requires_min_samples():
    Base.metadata.create_all(bind=engine)
    pipe = "testpipe-avg-unique"
    with SessionLocal() as db:
        u = models.User(username="qe_avg_user", password_hash="x")
        db.add(u); db.commit()
        base = datetime(2026, 2, 1, 0, 0, 0)
        # 3 completed jobs of 100s, 200s, 300s -> avg 200s
        for i, dur in enumerate((100, 200, 300)):
            c = base + timedelta(hours=i)
            _mk_job(db, u.id, pipeline=pipe, endpoint="e", status="completed",
                    created=c, updated=c + timedelta(seconds=dur))
        avgs = qe.average_durations(db)
        assert abs(avgs[pipe] - 200.0) < 1.0

        pipe2 = "testpipe-avg-toofew"
        c = base + timedelta(days=1)
        _mk_job(db, u.id, pipeline=pipe2, endpoint="e", status="completed",
                created=c, updated=c + timedelta(seconds=50))
        assert pipe2 not in qe.average_durations(db)  # only 1 sample


def test_list_jobs_endpoint_serializes_active_job():
    # Exercises the REAL endpoint (ORM -> JobRead -> JSON). This is the path that
    # 500'd in prod (from_orm on Pydantic v2); a pure-function test missed it.
    Base.metadata.create_all(bind=engine)
    from app.routers import jobs as jobs_router
    from app.schemas import JobRead
    with SessionLocal() as db:
        u = models.User(username="qe_list_user", password_hash="x")
        db.add(u); db.commit()
        _mk_job(db, u.id, pipeline="alphafold", endpoint="ep-list-test",
                status="in_progress", created=datetime(2026, 3, 1))
        result = jobs_router.list_jobs(db=db, current_user=u)
        assert len(result) == 1
        item = result[0]
        assert isinstance(item, JobRead)
        item.model_dump_json()  # must serialize without error
        assert item.queue_position is not None  # active job carries an estimate
