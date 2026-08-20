"""GET /api/admin/history -- everything ever run, newest first.

The filters here are the whole point of the page, so each one gets a case
that fails if it is dropped, and each one that is documented as
case-insensitive gets a case in the other case.
"""

from datetime import datetime

from sqlalchemy import text

from admin_helpers import client_as, job_row, make_user, run_row
from app.database import SessionLocal


def _set(db, table, row_id, **values):
    """Set columns the admin_helpers factories do not expose (finished_at,
    pipeline, error_message). Raw SQL so this test module does not have to
    reach past what those helpers guarantee about session state."""
    assignments = ", ".join(f"{column} = :{column}" for column in values)
    db.execute(
        text(f"UPDATE {table} SET {assignments} WHERE id = :row_id"),
        {**values, "row_id": row_id},
    )
    db.commit()


# --- the shape of the page -------------------------------------------------


def test_history_returns_every_account_newest_first():
    admin = make_user(is_admin=True)
    other = make_user(display_name="Ada")
    with SessionLocal() as db:
        job_row(db, other.id, "completed", title="older", created_at=datetime(2026, 1, 1))
        job_row(db, admin.id, "failed", title="newer", created_at=datetime(2026, 3, 1))

    body = client_as(admin).get("/api/admin/history").json()

    assert [item["name"] for item in body["items"]][:2] == ["newer", "older"]
    assert body["total"] >= 2


def test_history_filters_by_status_and_user():
    admin = make_user(is_admin=True)
    other = make_user(display_name="Grace Hopper")
    with SessionLocal() as db:
        job_row(db, other.id, "completed", title="hers")
        job_row(db, admin.id, "completed", title="mine")

    client = client_as(admin)
    by_user = client.get("/api/admin/history", params={"user": "grace"}).json()
    by_status = client.get("/api/admin/history", params={"status": "failed"}).json()

    assert [item["name"] for item in by_user["items"]] == ["hers"]
    assert by_status["items"] == []


def test_history_includes_workflow_runs_and_paginates():
    admin = make_user(is_admin=True)
    other = make_user()
    with SessionLocal() as db:
        run_row(db, other.id, "completed", name="a workflow")
        job_row(db, other.id, "completed", title="a job")

    client = client_as(admin)
    everything = client.get("/api/admin/history").json()
    page = client.get("/api/admin/history", params={"limit": 1, "offset": 1}).json()

    assert {item["kind"] for item in everything["items"]} == {"job", "workflow"}
    assert len(page["items"]) == 1
    assert page["items"][0]["name"] != everything["items"][0]["name"]


def test_history_lists_finished_work_that_activity_hides():
    # The distinction between the two pages: /activity is what is running,
    # /history is everything, so no status may be filtered out by default.
    admin = make_user(is_admin=True)
    other = make_user()
    with SessionLocal() as db:
        job_row(db, other.id, "completed", title="done", created_at=datetime(2026, 1, 4))
        job_row(db, other.id, "failed", title="broken", created_at=datetime(2026, 1, 3))
        job_row(db, other.id, "cancelled", title="stopped", created_at=datetime(2026, 1, 2))
        run_row(db, other.id, "completed", name="finished run", created_at=datetime(2026, 1, 1))

    items = client_as(admin).get("/api/admin/history").json()["items"]

    assert [i["name"] for i in items] == ["done", "broken", "stopped", "finished run"]


def test_history_reports_each_row_in_full():
    admin = make_user(is_admin=True)
    other = make_user(display_name="Ada Lovelace")
    with SessionLocal() as db:
        job_id = job_row(
            db, other.id, "failed", title="a fold",
            created_at=datetime(2026, 1, 5, 9, 0), updated_at=datetime(2026, 1, 5, 9, 30),
        ).id
        _set(db, "jobs", job_id, error_message="worker exploded")

    item = client_as(admin).get("/api/admin/history").json()["items"][0]

    assert item == {
        "kind": "job",
        "id": job_id,
        "owner": "Ada Lovelace",
        "name": "a fold",
        "pipeline": "alphafold",
        "status": "failed",
        "created_at": "2026-01-05T09:00:00",
        "duration_seconds": 1800,
        "error_message": "worker exploded",
    }


def test_history_reports_a_run_in_full():
    admin = make_user(is_admin=True)
    other = make_user(display_name="Ada Lovelace")
    with SessionLocal() as db:
        run_id = run_row(
            db, other.id, "failed", name="a workflow",
            created_at=datetime(2026, 1, 2, 9, 0), started_at=datetime(2026, 1, 2, 10, 0),
        ).id
        _set(db, "workflow_runs", run_id,
             finished_at=datetime(2026, 1, 2, 10, 5), error_message="step 2 failed")

    item = client_as(admin).get("/api/admin/history").json()["items"][0]

    assert item == {
        "kind": "workflow",
        "id": run_id,
        "owner": "Ada Lovelace",
        "name": "a workflow",
        "pipeline": "rapid_v1",
        "status": "failed",
        # created_at, not started_at: the two are an hour apart on purpose.
        "created_at": "2026-01-02T09:00:00",
        "duration_seconds": 300,
        "error_message": "step 2 failed",
    }


def test_history_leaks_no_private_bookkeeping_keys():
    # The row dicts carry the owner object so the user filter can read every
    # name it has; a leaked SQLAlchemy object would not serialise at all.
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        job_row(db, admin.id, "completed", title="mine")
        run_row(db, admin.id, "completed", name="ours")

    items = client_as(admin).get("/api/admin/history").json()["items"]

    assert items
    for item in items:
        assert [key for key in item if key.startswith("_")] == []


def test_history_is_admin_only():
    nobody = make_user(is_admin=False)

    assert client_as(nobody).get("/api/admin/history").status_code == 403


# --- durations -------------------------------------------------------------


def test_a_running_job_has_no_duration():
    # updated_at moves every time the poller touches the row, so on an
    # unfinished job it is "when we last heard", not "when it ended".
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        job_row(db, admin.id, "running", title="in flight",
                created_at=datetime(2026, 1, 1, 9, 0), updated_at=datetime(2026, 1, 1, 9, 30))
        job_row(db, admin.id, "queued", title="waiting",
                created_at=datetime(2026, 1, 1, 8, 0), updated_at=datetime(2026, 1, 1, 9, 30))

    by_name = {i["name"]: i for i in client_as(admin).get("/api/admin/history").json()["items"]}

    assert by_name["in flight"]["duration_seconds"] is None
    assert by_name["waiting"]["duration_seconds"] is None


def test_a_finished_job_measures_created_to_updated():
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        for status in ("completed", "failed", "cancelled"):
            job_row(db, admin.id, status, title=status,
                    created_at=datetime(2026, 1, 1, 9, 0), updated_at=datetime(2026, 1, 1, 9, 10))

    by_name = {i["name"]: i for i in client_as(admin).get("/api/admin/history").json()["items"]}

    assert [by_name[s]["duration_seconds"] for s in ("completed", "failed", "cancelled")] == [600] * 3


def test_a_shouting_terminal_job_status_still_measures_its_duration():
    # Job statuses come from external workers, so the terminal check is
    # case-insensitive exactly like the one in /activity.
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        job_row(db, admin.id, "COMPLETED", title="loud",
                created_at=datetime(2026, 1, 1, 9, 0), updated_at=datetime(2026, 1, 1, 9, 10))

    items = client_as(admin).get("/api/admin/history").json()["items"]

    assert items[0]["duration_seconds"] == 600


def test_a_run_that_never_started_has_no_duration():
    # No fallback to created_at here: a queued run has not spent any time
    # running, and reporting its age as a duration would inflate every total
    # Task 6 builds on top of these rows.
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        run_row(db, admin.id, "queued", name="never ran", created_at=datetime(2026, 1, 2, 9, 0))

    items = client_as(admin).get("/api/admin/history").json()["items"]

    assert items[0]["status"] == "queued"
    assert items[0]["duration_seconds"] is None


def test_a_run_cancelled_before_it_started_has_no_duration():
    # started_at NULL but finished_at set: a run killed while still queued.
    # Measuring created_at -> finished_at here would bill the queue wait as
    # run time, and a run that never ran has no run time to report.
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        run_id = run_row(db, admin.id, "cancelled", name="killed in the queue",
                         created_at=datetime(2026, 1, 2, 9, 0)).id
        _set(db, "workflow_runs", run_id,
             started_at=None, finished_at=datetime(2026, 1, 2, 9, 30))

    items = client_as(admin).get("/api/admin/history").json()["items"]

    assert items[0]["name"] == "killed in the queue"
    assert items[0]["duration_seconds"] is None


def test_a_run_still_going_has_no_duration():
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        run_row(db, admin.id, "running", name="still going",
                created_at=datetime(2026, 1, 2, 9, 0), started_at=datetime(2026, 1, 2, 10, 0))

    items = client_as(admin).get("/api/admin/history").json()["items"]

    assert items[0]["duration_seconds"] is None


def test_a_finished_run_measures_started_to_finished():
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        run_id = run_row(db, admin.id, "completed", name="ran",
                         created_at=datetime(2026, 1, 2, 9, 0),
                         started_at=datetime(2026, 1, 2, 10, 0)).id
        _set(db, "workflow_runs", run_id, finished_at=datetime(2026, 1, 2, 10, 30))

    items = client_as(admin).get("/api/admin/history").json()["items"]

    # 30 minutes of running, not the 90 since it was created.
    assert items[0]["duration_seconds"] == 1800


# --- filters ---------------------------------------------------------------


def test_the_user_filter_reads_every_name_an_account_has():
    admin = make_user(is_admin=True)
    # make_user generates the username, so this one is renamed in place; the
    # real ones are OIDC subjects, which is why the other two names matter.
    by_username = make_user()
    by_email = make_user(email="trillian@heartofgold.example")
    by_display = make_user(display_name="Arthur Dent")
    with SessionLocal() as db:
        _set(db, "users", by_username.id, username="zaphod_beeblebrox")
        job_row(db, by_username.id, "completed", title="username hit")
        job_row(db, by_email.id, "completed", title="email hit")
        job_row(db, by_display.id, "completed", title="display hit")
        job_row(db, admin.id, "completed", title="not a hit")

    client = client_as(admin)
    hits = {
        term: [i["name"] for i in client.get("/api/admin/history", params={"user": term}).json()["items"]]
        for term in ("zaphod", "heartofgold", "Arthur")
    }

    assert hits == {
        "zaphod": ["username hit"],
        "heartofgold": ["email hit"],
        "Arthur": ["display hit"],
    }


def test_the_user_filter_ignores_case_in_both_directions():
    admin = make_user(is_admin=True)
    other = make_user(display_name="Grace Hopper")
    with SessionLocal() as db:
        job_row(db, other.id, "completed", title="hers")

    client = client_as(admin)
    shouted = client.get("/api/admin/history", params={"user": "GRACE"}).json()
    whispered = client.get("/api/admin/history", params={"user": "hopper"}).json()

    assert [i["name"] for i in shouted["items"]] == ["hers"]
    assert [i["name"] for i in whispered["items"]] == ["hers"]


def test_an_empty_filter_value_filters_nothing():
    # The UI sends its filter boxes on every request, so blank must mean "all"
    # rather than "match the empty string against a null pipeline".
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        job_row(db, admin.id, "completed", title="mine")
        run_row(db, admin.id, "completed", name="ours")

    params = {"user": "", "pipeline": "", "status": "", "since": ""}
    body = client_as(admin).get("/api/admin/history", params=params).json()

    assert {i["name"] for i in body["items"]} == {"mine", "ours"}


def test_the_pipeline_filter_is_exact_but_ignores_case():
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        _set(db, "jobs", job_row(db, admin.id, "completed", title="folded").id,
             pipeline="AlphaFold")
        _set(db, "jobs", job_row(db, admin.id, "completed", title="docked").id,
             pipeline="alphafold_multimer")

    client = client_as(admin)
    exact = client.get("/api/admin/history", params={"pipeline": "alphafold"}).json()
    partial = client.get("/api/admin/history", params={"pipeline": "alpha"}).json()

    assert [i["name"] for i in exact["items"]] == ["folded"]
    assert partial["items"] == []


def test_the_pipeline_filter_matches_a_workflow_template_key():
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        run_row(db, admin.id, "completed", name="a run")
        job_row(db, admin.id, "completed", title="a job")

    body = client_as(admin).get("/api/admin/history", params={"pipeline": "RAPID_V1"}).json()

    assert [i["name"] for i in body["items"]] == ["a run"]


def test_the_status_filter_is_exact_but_ignores_case():
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        job_row(db, admin.id, "COMPLETED", title="loud")
        job_row(db, admin.id, "completed_with_warnings", title="nearly")

    body = client_as(admin).get("/api/admin/history", params={"status": "completed"}).json()

    assert [i["name"] for i in body["items"]] == ["loud"]


def test_the_filters_combine():
    admin = make_user(is_admin=True)
    other = make_user(display_name="Grace Hopper")
    with SessionLocal() as db:
        job_row(db, other.id, "completed", title="hers done")
        job_row(db, other.id, "failed", title="hers broken")
        job_row(db, admin.id, "completed", title="mine done")

    body = client_as(admin).get(
        "/api/admin/history", params={"user": "hopper", "status": "completed"}
    ).json()

    assert [i["name"] for i in body["items"]] == ["hers done"]


def test_since_keeps_rows_created_at_or_after_it():
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        job_row(db, admin.id, "completed", title="after", created_at=datetime(2026, 2, 2))
        job_row(db, admin.id, "completed", title="on the boundary", created_at=datetime(2026, 2, 1))
        job_row(db, admin.id, "completed", title="before", created_at=datetime(2026, 1, 31))

    body = client_as(admin).get(
        "/api/admin/history", params={"since": "2026-02-01T00:00:00"}
    ).json()

    assert [i["name"] for i in body["items"]] == ["after", "on the boundary"]
    assert body["total"] == 2


def test_since_accepts_a_bare_date_and_a_zulu_timestamp():
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        job_row(db, admin.id, "completed", title="after", created_at=datetime(2026, 2, 2))
        job_row(db, admin.id, "completed", title="before", created_at=datetime(2026, 1, 1))

    client = client_as(admin)
    bare = client.get("/api/admin/history", params={"since": "2026-02-01"}).json()
    zulu = client.get("/api/admin/history", params={"since": "2026-02-01T00:00:00Z"}).json()

    assert [i["name"] for i in bare["items"]] == ["after"]
    assert [i["name"] for i in zulu["items"]] == ["after"]


def test_since_rejects_a_value_it_cannot_parse():
    # A filter box a human types into: a typo is a bad request, not a crash.
    admin = make_user(is_admin=True)

    response = client_as(admin).get("/api/admin/history", params={"since": "last tuesday"})

    assert response.status_code == 400
    assert "since" in response.json()["detail"]


# --- pagination ------------------------------------------------------------


def test_total_counts_the_matches_before_pagination():
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        for i in range(5):
            job_row(db, admin.id, "completed", title=f"j{i}", created_at=datetime(2026, 1, i + 1))

    body = client_as(admin).get("/api/admin/history", params={"limit": 2}).json()

    assert len(body["items"]) == 2
    assert body["total"] == 5


def test_offset_walks_the_list():
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        for i in range(4):
            job_row(db, admin.id, "completed", title=f"j{i}", created_at=datetime(2026, 1, i + 1))

    client = client_as(admin)
    page = client.get("/api/admin/history", params={"limit": 2, "offset": 2}).json()

    # Newest first: j3, j2 | j1, j0.
    assert [i["name"] for i in page["items"]] == ["j1", "j0"]
    assert page["offset"] == 2
    assert page["limit"] == 2


def test_an_offset_past_the_end_is_an_empty_page_not_an_error():
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        job_row(db, admin.id, "completed", title="only")

    body = client_as(admin).get("/api/admin/history", params={"offset": 50}).json()

    assert body["items"] == []
    assert body["total"] == 1


def test_pagination_defaults_are_reported():
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        job_row(db, admin.id, "completed", title="only")

    body = client_as(admin).get("/api/admin/history").json()

    assert body["limit"] == 100
    assert body["offset"] == 0


def test_an_outsized_limit_is_clamped():
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        job_row(db, admin.id, "completed", title="only")

    body = client_as(admin).get("/api/admin/history", params={"limit": 10000}).json()

    assert body["limit"] == 500


def test_a_nonsense_limit_or_offset_is_clamped_not_obeyed():
    # A zero limit would render an empty page forever and a negative offset
    # would slice from the end of the list, showing the oldest rows first.
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        for i in range(3):
            job_row(db, admin.id, "completed", title=f"j{i}", created_at=datetime(2026, 1, i + 1))

    client = client_as(admin)
    zero = client.get("/api/admin/history", params={"limit": 0}).json()
    negative_limit = client.get("/api/admin/history", params={"limit": -5}).json()
    negative_offset = client.get("/api/admin/history", params={"offset": -2}).json()

    assert zero["limit"] == 1 and [i["name"] for i in zero["items"]] == ["j2"]
    assert negative_limit["limit"] == 1 and [i["name"] for i in negative_limit["items"]] == ["j2"]
    assert negative_offset["offset"] == 0
    assert [i["name"] for i in negative_offset["items"]] == ["j2", "j1", "j0"]


# --- rows nothing else would show ------------------------------------------


def test_jobs_and_runs_are_interleaved_by_creation_time():
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        job_row(db, admin.id, "completed", title="newest", created_at=datetime(2026, 1, 3))
        job_row(db, admin.id, "completed", title="oldest", created_at=datetime(2026, 1, 1))
        run_row(db, admin.id, "completed", name="middle", created_at=datetime(2026, 1, 2))

    items = client_as(admin).get("/api/admin/history").json()["items"]

    assert [i["name"] for i in items] == ["newest", "middle", "oldest"]


def test_a_job_whose_owner_row_is_gone_is_still_listed():
    admin = make_user(is_admin=True)
    other = make_user(display_name="Ada Lovelace")
    with SessionLocal() as db:
        job_row(db, other.id, "completed", title="orphaned fold")
        db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": other.id})
        db.commit()

    items = client_as(admin).get("/api/admin/history").json()["items"]

    assert [(i["name"], i["owner"]) for i in items] == [("orphaned fold", "(unknown)")]


def test_a_run_whose_owner_row_is_gone_is_still_listed():
    admin = make_user(is_admin=True)
    other = make_user(display_name="Ada Lovelace")
    with SessionLocal() as db:
        run_row(db, other.id, "completed", name="orphaned workflow")
        db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": other.id})
        db.commit()

    items = client_as(admin).get("/api/admin/history").json()["items"]

    assert [(i["name"], i["owner"]) for i in items] == [("orphaned workflow", "(unknown)")]


def test_an_owner_less_row_does_not_break_the_user_filter():
    # The filter reads names off the owner object, which is None here.
    admin = make_user(is_admin=True)
    other = make_user(display_name="Ada Lovelace")
    with SessionLocal() as db:
        job_row(db, other.id, "completed", title="orphaned fold")
        db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": other.id})
        db.commit()

    body = client_as(admin).get("/api/admin/history", params={"user": "ada"})

    assert body.status_code == 200
    assert body.json()["items"] == []


def test_a_run_whose_workflow_row_is_gone_still_names_itself():
    admin = make_user(is_admin=True)
    other = make_user(display_name="Ada Lovelace")
    with SessionLocal() as db:
        workflow_id = run_row(db, other.id, "completed", name="doomed").workflow_id
        db.execute(text("DELETE FROM workflows WHERE id = :wid"), {"wid": workflow_id})
        db.commit()

    items = client_as(admin).get("/api/admin/history").json()["items"]

    assert [i["name"] for i in items] == ["(deleted workflow)"]
    assert items[0]["pipeline"] is None
    assert items[0]["owner"] == "Ada Lovelace"


def test_a_job_without_a_title_falls_back_to_its_pipeline():
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        job_row(db, admin.id, "completed", title="")

    items = client_as(admin).get("/api/admin/history").json()["items"]

    assert [i["name"] for i in items] == ["alphafold"]
