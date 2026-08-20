from datetime import datetime, timedelta

from admin_helpers import client_as, job_row, make_user, run_row
from app.database import SessionLocal


def test_activity_lists_other_users_running_work():
    admin = make_user(is_admin=True)
    other = make_user(display_name="Ada Lovelace")
    with SessionLocal() as db:
        job_row(db, other.id, "running", title="their fold")
        job_row(db, other.id, "completed", title="finished")
        run_row(db, other.id, "running", name="their workflow")

    items = client_as(admin).get("/api/admin/activity").json()["items"]

    names = {item["name"] for item in items}
    assert "their fold" in names
    assert "their workflow" in names
    assert "finished" not in names
    assert {item["owner"] for item in items} == {"Ada Lovelace"}
    assert {item["kind"] for item in items} == {"job", "workflow"}


def test_activity_reports_elapsed_seconds():
    admin = make_user(is_admin=True)
    other = make_user()
    started = datetime.utcnow() - timedelta(seconds=120)
    with SessionLocal() as db:
        job_row(db, other.id, "running", created_at=started)

    items = client_as(admin).get("/api/admin/activity").json()["items"]

    assert items[0]["elapsed_seconds"] >= 100


def test_finished_workflow_runs_are_left_out():
    admin = make_user(is_admin=True)
    other = make_user()
    with SessionLocal() as db:
        run_row(db, other.id, "queued", name="still waiting")
        run_row(db, other.id, "completed", name="done")
        run_row(db, other.id, "failed", name="broken")
        run_row(db, other.id, "cancelled", name="abandoned")

    items = client_as(admin).get("/api/admin/activity").json()["items"]

    assert [item["name"] for item in items] == ["still waiting"]


def test_jobs_and_runs_are_interleaved_newest_first():
    admin = make_user(is_admin=True)
    other = make_user()
    with SessionLocal() as db:
        # run_row starts its run at 2026-01-02 10:00, so the jobs straddle it.
        job_row(db, other.id, "running", title="newest", created_at=datetime(2026, 1, 3))
        job_row(db, other.id, "queued", title="oldest", created_at=datetime(2026, 1, 1))
        run_row(db, other.id, "running", name="middle")

    items = client_as(admin).get("/api/admin/activity").json()["items"]

    assert [item["name"] for item in items] == ["newest", "middle", "oldest"]


def test_elapsed_survives_an_estimate_without_one(monkeypatch):
    # queue_estimate always fills elapsed_seconds today, but the endpoint must
    # not report null if that ever stops being true -- dict.get(key, default)
    # would happily hand back a present-but-None value.
    from app import queue_estimate

    monkeypatch.setattr(
        queue_estimate, "estimate_for_job", lambda *a, **k: {"elapsed_seconds": None}
    )
    admin = make_user(is_admin=True)
    other = make_user()
    started = datetime.utcnow() - timedelta(seconds=300)
    with SessionLocal() as db:
        job_row(db, other.id, "running", created_at=started)

    items = client_as(admin).get("/api/admin/activity").json()["items"]

    assert items[0]["elapsed_seconds"] >= 250


def test_a_queued_run_falls_back_to_when_it_was_created():
    # started_at is only set once a run actually starts, so every queued run in
    # production reaches this fallback. Without it a queued run would report a
    # null start, show no elapsed time, and sink to the bottom of the list.
    admin = make_user(is_admin=True)
    other = make_user()
    with SessionLocal() as db:
        run_row(db, other.id, "queued", created_at=datetime(2026, 1, 2, 9, 0), started_at=None)

    items = client_as(admin).get("/api/admin/activity").json()["items"]

    assert items[0]["started_at"] == "2026-01-02T09:00:00"
    assert items[0]["elapsed_seconds"] > 0


def test_a_running_run_reports_when_it_started_not_when_it_was_queued():
    admin = make_user(is_admin=True)
    other = make_user()
    with SessionLocal() as db:
        run_row(
            db, other.id, "running",
            created_at=datetime(2026, 1, 2, 9, 0), started_at=datetime(2026, 1, 2, 10, 0),
        )

    items = client_as(admin).get("/api/admin/activity").json()["items"]

    assert items[0]["started_at"] == "2026-01-02T10:00:00"
    # A multi-step run sits in no single endpoint queue; the UI renders these
    # two fields, so "no estimate" must stay null rather than become a zero.
    assert items[0]["queue_position"] is None
    assert items[0]["eta_seconds"] is None
