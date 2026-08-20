from datetime import datetime, timedelta

from sqlalchemy import text

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

    # White-box: this patches by name, so a refactor that stops calling
    # estimate_for_job leaves it intercepting nothing and passing on the real
    # value. The is-not-None assertion below keeps it honest in that case.
    monkeypatch.setattr(
        queue_estimate, "estimate_for_job", lambda *a, **k: {"elapsed_seconds": None}
    )
    admin = make_user(is_admin=True)
    other = make_user()
    started = datetime.utcnow() - timedelta(seconds=300)
    with SessionLocal() as db:
        job_row(db, other.id, "running", created_at=started)

    items = client_as(admin).get("/api/admin/activity").json()["items"]

    assert items[0]["elapsed_seconds"] is not None
    assert items[0]["elapsed_seconds"] >= 250


def test_a_queued_run_falls_back_to_when_it_was_created():
    # started_at is only set once a run actually starts, so every queued run in
    # production reaches this fallback. Without it a queued run would report a
    # null start, show no elapsed time, and sink to the bottom of the list.
    admin = make_user(is_admin=True)
    other = make_user()
    with SessionLocal() as db:
        # run_row leaves started_at NULL for a queued run, as production does.
        run_row(db, other.id, "queued", created_at=datetime(2026, 1, 2, 9, 0))

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


def test_a_queued_job_reports_its_place_in_the_endpoint_queue():
    admin = make_user(is_admin=True)
    other = make_user()
    with SessionLocal() as db:
        for i in range(3):  # average_durations needs 3 samples before it will estimate
            job_row(db, other.id, "completed", title=f"h{i}",
                    created_at=datetime(2025, 12, i + 1),
                    updated_at=datetime(2025, 12, i + 1) + timedelta(seconds=600))
        job_row(db, other.id, "running", title="first", created_at=datetime(2026, 1, 1, 9))
        job_row(db, other.id, "queued", title="second", created_at=datetime(2026, 1, 1, 10))

    by_name = {i["name"]: i for i in client_as(admin).get("/api/admin/activity").json()["items"]}
    assert by_name["first"]["queue_position"] == 0
    assert by_name["second"]["queue_position"] == 1
    assert by_name["second"]["eta_seconds"] > 0


def test_a_job_whose_owner_row_is_gone_is_still_listed():
    # Nothing deletes users today and PRAGMA foreign_keys is 0, so an orphan is
    # possible and this page must not be the thing that hides it. Deleted in
    # raw SQL because User.jobs cascades delete-orphan through the ORM.
    admin = make_user(is_admin=True)
    other = make_user(display_name="Ada Lovelace")
    with SessionLocal() as db:
        job_row(db, other.id, "running", title="orphaned fold")
        db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": other.id})
        db.commit()

    items = client_as(admin).get("/api/admin/activity").json()["items"]

    assert [(i["name"], i["owner"]) for i in items] == [("orphaned fold", "(unknown)")]


def test_a_mixed_case_job_status_is_still_recognised_as_active():
    # Statuses come from external workers, not from this codebase.
    admin = make_user(is_admin=True)
    other = make_user()
    with SessionLocal() as db:
        job_row(db, other.id, "IN_PROGRESS", title="shouting worker")

    items = client_as(admin).get("/api/admin/activity").json()["items"]

    assert [i["name"] for i in items] == ["shouting worker"]


def test_an_unrecognised_run_status_stays_visible():
    # The run filter excludes the finished statuses rather than listing the
    # active ones, so a status nobody anticipated looks odd here instead of
    # dropping off the page an admin uses to see everything.
    admin = make_user(is_admin=True)
    other = make_user()
    with SessionLocal() as db:
        run_row(db, other.id, "paused", name="who knows")

    items = client_as(admin).get("/api/admin/activity").json()["items"]

    assert [i["name"] for i in items] == ["who knows"]


def test_a_run_whose_owner_row_is_gone_is_still_listed():
    # Same orphan reasoning as the job side; the run half joins users too.
    admin = make_user(is_admin=True)
    other = make_user(display_name="Ada Lovelace")
    with SessionLocal() as db:
        run_row(db, other.id, "running", name="orphaned workflow")
        db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": other.id})
        db.commit()

    items = client_as(admin).get("/api/admin/activity").json()["items"]

    assert [(i["name"], i["owner"]) for i in items] == [("orphaned workflow", "(unknown)")]


def test_a_run_whose_workflow_row_is_gone_still_names_itself():
    admin = make_user(is_admin=True)
    other = make_user(display_name="Ada Lovelace")
    with SessionLocal() as db:
        run = run_row(db, other.id, "running", name="doomed")
        db.execute(text("DELETE FROM workflows WHERE id = :wid"), {"wid": run.workflow_id})
        db.commit()

    items = client_as(admin).get("/api/admin/activity").json()["items"]

    assert [i["name"] for i in items] == ["(deleted workflow)"]
    assert items[0]["pipeline"] is None
    assert items[0]["owner"] == "Ada Lovelace"


def test_an_idle_fleet_does_not_scan_for_averages(monkeypatch):
    # Performance-only, so nothing in the response can show it: with no active
    # jobs there is nothing to estimate, and this page is polled every 5s.
    from app import queue_estimate

    calls = []
    monkeypatch.setattr(queue_estimate, "average_durations", lambda *a, **k: calls.append(1) or {})
    admin = make_user(is_admin=True)
    other = make_user()
    with SessionLocal() as db:
        job_row(db, other.id, "completed", title="long done")
        run_row(db, other.id, "running", name="still going")

    items = client_as(admin).get("/api/admin/activity").json()["items"]

    assert [i["name"] for i in items] == ["still going"]
    assert calls == []
