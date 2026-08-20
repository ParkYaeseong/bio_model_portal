"""GET /api/admin/history -- everything ever run, newest first.

The filters here are the whole point of the page, so each one gets a case
that fails if it is dropped, and each one that is documented as
case-insensitive, padding-tolerant or exact gets a case in the other
direction. Each test asserts one contract; the response envelope is read
through the `_history` helper so a change to its shape is one edit.
"""

import logging
from datetime import datetime

from sqlalchemy import text

from admin_helpers import client_as, job_row, make_user, run_row
from app.database import SessionLocal

# Every key an item is expected to publish. Spelled out rather than derived
# from the code's own private-key rule: a future private key that forgets its
# underscore has to fail here.
PUBLIC_KEYS = {
    "kind", "id", "owner_id", "owner", "name", "pipeline",
    "status", "created_at", "duration_seconds", "error_message",
}


def _history(admin, **params) -> dict:
    """The response body of one successful /api/admin/history call."""
    response = client_as(admin).get("/api/admin/history", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _set(db, table, row_id, **values):
    """Set columns the admin_helpers factories do not expose (finished_at,
    pipeline, error_message, username).

    test-only: table and column names are literals here, never request data.
    """
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

    body = _history(admin)

    assert [item["name"] for item in body["items"]] == ["newer", "older"]
    assert body["total"] == 2


def test_history_lists_finished_work_that_activity_hides():
    # The distinction between the two pages: /activity is what is running,
    # /history is everything, so no status may be filtered out by default and
    # both halves of the union have to be in it.
    admin = make_user(is_admin=True)
    other = make_user()
    with SessionLocal() as db:
        job_row(db, other.id, "completed", title="done", created_at=datetime(2026, 1, 4))
        job_row(db, other.id, "failed", title="broken", created_at=datetime(2026, 1, 3))
        job_row(db, other.id, "cancelled", title="stopped", created_at=datetime(2026, 1, 2))
        run_row(db, other.id, "completed", name="finished run", created_at=datetime(2026, 1, 1))

    items = _history(admin)["items"]

    assert [i["name"] for i in items] == ["done", "broken", "stopped", "finished run"]
    assert {i["kind"] for i in items} == {"job", "workflow"}


def test_history_reports_a_job_in_full():
    admin = make_user(is_admin=True)
    other = make_user(display_name="Ada Lovelace")
    with SessionLocal() as db:
        job_id = job_row(
            db, other.id, "failed", title="a fold",
            created_at=datetime(2026, 1, 5, 9, 0), updated_at=datetime(2026, 1, 5, 9, 30),
        ).id
        _set(db, "jobs", job_id, error_message="worker exploded")

    item = _history(admin)["items"][0]

    assert item == {
        "kind": "job",
        "id": job_id,
        "owner_id": other.id,
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

    item = _history(admin)["items"][0]

    assert item == {
        "kind": "workflow",
        "id": run_id,
        "owner_id": other.id,
        "owner": "Ada Lovelace",
        "name": "a workflow",
        "pipeline": "rapid_v1",
        "status": "failed",
        # created_at, not started_at: the two are an hour apart on purpose.
        "created_at": "2026-01-02T09:00:00",
        "duration_seconds": 300,
        "error_message": "step 2 failed",
    }


def test_history_publishes_exactly_the_public_keys():
    # The row dicts carry the owner object so the user filter can read every
    # name it has; a leaked SQLAlchemy object would not serialise at all.
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        job_row(db, admin.id, "completed", title="mine")
        run_row(db, admin.id, "completed", name="ours")

    items = _history(admin)["items"]

    assert len(items) == 2
    for item in items:
        assert set(item) == PUBLIC_KEYS


def test_owner_id_tells_two_deleted_accounts_apart():
    # Both rows render as "(unknown)", so a per-user view that grouped on the
    # display label would fold two different people into one bucket.
    admin = make_user(is_admin=True)
    one = make_user(display_name="Ada Lovelace")
    two = make_user(display_name="Grace Hopper")
    with SessionLocal() as db:
        job_row(db, one.id, "completed", title="hers", created_at=datetime(2026, 1, 2))
        job_row(db, two.id, "completed", title="theirs", created_at=datetime(2026, 1, 1))
        db.execute(text("DELETE FROM users WHERE id IN (:a, :b)"), {"a": one.id, "b": two.id})
        db.commit()

    items = _history(admin)["items"]

    assert [i["owner"] for i in items] == ["(unknown)", "(unknown)"]
    assert [i["owner_id"] for i in items] == [one.id, two.id]


def test_history_is_admin_only():
    nobody = make_user(is_admin=False)

    assert client_as(nobody).get("/api/admin/history").status_code == 403


def test_an_oversized_scan_is_warned_about(monkeypatch, caplog):
    # Nothing in the response can show this: the in-Python scan is the right
    # design at 25 rows and the wrong one at 50k, and this is the only thing
    # that will say so.
    from app.routers import admin

    monkeypatch.setattr(admin, "HISTORY_SCAN_WARN_ROWS", 0)
    owner = make_user(is_admin=True)
    with SessionLocal() as db:
        job_row(db, owner.id, "completed", title="only")

    with caplog.at_level(logging.WARNING, logger="app.routers.admin"):
        assert len(_history(owner)["items"]) == 1

    assert [r.getMessage() for r in caplog.records if "scanned 1 rows" in r.getMessage()]


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

    by_name = {i["name"]: i for i in _history(admin)["items"]}

    assert by_name["in flight"]["duration_seconds"] is None
    assert by_name["waiting"]["duration_seconds"] is None


def test_a_finished_job_measures_created_to_updated():
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        for status in ("completed", "failed", "cancelled"):
            job_row(db, admin.id, status, title=status,
                    created_at=datetime(2026, 1, 1, 9, 0), updated_at=datetime(2026, 1, 1, 9, 10))

    by_name = {i["name"]: i for i in _history(admin)["items"]}

    assert [by_name[s]["duration_seconds"] for s in ("completed", "failed", "cancelled")] == [600] * 3


def test_a_shouting_terminal_job_status_still_measures_its_duration():
    # Job statuses come from external workers, so the terminal check is
    # case-insensitive exactly like the one in /activity.
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        job_row(db, admin.id, "COMPLETED", title="loud",
                created_at=datetime(2026, 1, 1, 9, 0), updated_at=datetime(2026, 1, 1, 9, 10))

    items = _history(admin)["items"]

    assert items[0]["duration_seconds"] == 600


def test_a_run_that_never_started_has_no_duration():
    # No fallback to created_at here: a queued run has not spent any time
    # running, and reporting its age as a duration would inflate every total
    # the per-user view builds on these rows.
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        run_row(db, admin.id, "queued", name="never ran", created_at=datetime(2026, 1, 2, 9, 0))

    items = _history(admin)["items"]

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

    items = _history(admin)["items"]

    assert items[0]["name"] == "killed in the queue"
    assert items[0]["duration_seconds"] is None


def test_a_run_still_going_has_no_duration():
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        run_row(db, admin.id, "running", name="still going",
                created_at=datetime(2026, 1, 2, 9, 0), started_at=datetime(2026, 1, 2, 10, 0))

    items = _history(admin)["items"]

    assert items[0]["duration_seconds"] is None


def test_a_finished_run_measures_started_to_finished():
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        run_id = run_row(db, admin.id, "completed", name="ran",
                         created_at=datetime(2026, 1, 2, 9, 0),
                         started_at=datetime(2026, 1, 2, 10, 0)).id
        _set(db, "workflow_runs", run_id, finished_at=datetime(2026, 1, 2, 10, 30))

    items = _history(admin)["items"]

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

    hits = {
        term: [i["name"] for i in _history(admin, user=term)["items"]]
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

    assert [i["name"] for i in _history(admin, user="GRACE")["items"]] == ["hers"]
    assert [i["name"] for i in _history(admin, user="hopper")["items"]] == ["hers"]


def test_the_user_filter_does_not_match_across_two_fields():
    # Joining the names into one string would let 'zz alice' match a 'zz'
    # username sitting next to an 'alice@...' email: a false positive on an
    # audit filter, which is worse than a miss.
    admin = make_user(is_admin=True)
    other = make_user(email="alice@example.test")
    with SessionLocal() as db:
        _set(db, "users", other.id, username="zz")
        job_row(db, other.id, "completed", title="hers")

    assert [i["name"] for i in _history(admin, user="zz")["items"]] == ["hers"]
    assert [i["name"] for i in _history(admin, user="alice")["items"]] == ["hers"]
    assert _history(admin, user="zz alice")["items"] == []


def test_an_empty_filter_value_filters_nothing():
    # The UI sends its filter boxes on every request, so blank must mean "all"
    # rather than "match the empty string against a null pipeline".
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        job_row(db, admin.id, "completed", title="mine")
        run_row(db, admin.id, "completed", name="ours")

    body = _history(admin, user="", pipeline="", status="", since="")

    assert {i["name"] for i in body["items"]} == {"mine", "ours"}


def test_a_whitespace_only_filter_value_also_filters_nothing():
    # All four boxes have to agree: a stray space must not make one of them
    # match nothing while another answers 400.
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        job_row(db, admin.id, "completed", title="mine")
        run_row(db, admin.id, "completed", name="ours")

    body = _history(admin, user="  ", pipeline="  ", status="  ", since="  ")

    assert {i["name"] for i in body["items"]} == {"mine", "ours"}


def test_the_filters_ignore_padding_around_a_real_value():
    admin = make_user(is_admin=True)
    other = make_user(display_name="Grace Hopper")
    with SessionLocal() as db:
        job_row(db, other.id, "completed", title="hers")
        job_row(db, admin.id, "failed", title="mine")

    assert [i["name"] for i in _history(admin, user=" grace ")["items"]] == ["hers"]
    assert [i["name"] for i in _history(admin, status=" completed ")["items"]] == ["hers"]
    assert {i["name"] for i in _history(admin, pipeline=" alphafold ")["items"]} == {"hers", "mine"}


def test_the_pipeline_filter_is_exact_but_ignores_case():
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        _set(db, "jobs", job_row(db, admin.id, "completed", title="folded").id,
             pipeline="AlphaFold")
        _set(db, "jobs", job_row(db, admin.id, "completed", title="docked").id,
             pipeline="alphafold_multimer")

    assert [i["name"] for i in _history(admin, pipeline="alphafold")["items"]] == ["folded"]
    assert _history(admin, pipeline="alpha")["items"] == []


def test_the_pipeline_filter_matches_a_workflow_template_key():
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        run_row(db, admin.id, "completed", name="a run")
        job_row(db, admin.id, "completed", title="a job")

    assert [i["name"] for i in _history(admin, pipeline="RAPID_V1")["items"]] == ["a run"]


def test_a_row_with_no_pipeline_matches_no_pipeline_filter():
    # A run whose workflow row is gone reports a null pipeline; it must not
    # fall into some other pipeline's results, and must not crash the filter.
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        workflow_id = run_row(db, admin.id, "completed", name="doomed").workflow_id
        db.execute(text("DELETE FROM workflows WHERE id = :wid"), {"wid": workflow_id})
        db.commit()

    assert [i["name"] for i in _history(admin)["items"]] == ["(deleted workflow)"]
    assert _history(admin, pipeline="rapid_v1")["items"] == []


def test_the_status_filter_is_exact_but_ignores_case():
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        job_row(db, admin.id, "COMPLETED", title="loud")
        job_row(db, admin.id, "completed_with_warnings", title="nearly")

    assert [i["name"] for i in _history(admin, status="completed")["items"]] == ["loud"]


def test_the_filters_combine():
    admin = make_user(is_admin=True)
    other = make_user(display_name="Grace Hopper")
    with SessionLocal() as db:
        job_row(db, other.id, "completed", title="hers done")
        job_row(db, other.id, "failed", title="hers broken")
        job_row(db, admin.id, "completed", title="mine done")

    body = _history(admin, user="hopper", status="completed")

    assert [i["name"] for i in body["items"]] == ["hers done"]


def test_since_keeps_rows_created_at_or_after_it():
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        job_row(db, admin.id, "completed", title="after", created_at=datetime(2026, 2, 2))
        job_row(db, admin.id, "completed", title="on the boundary", created_at=datetime(2026, 2, 1))
        job_row(db, admin.id, "completed", title="before", created_at=datetime(2026, 1, 31))

    body = _history(admin, since="2026-02-01T00:00:00")

    assert [i["name"] for i in body["items"]] == ["after", "on the boundary"]
    assert body["total"] == 2


def test_since_accepts_a_bare_date_and_a_zulu_timestamp():
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        job_row(db, admin.id, "completed", title="after", created_at=datetime(2026, 2, 2))
        job_row(db, admin.id, "completed", title="before", created_at=datetime(2026, 1, 1))

    assert [i["name"] for i in _history(admin, since="2026-02-01")["items"]] == ["after"]
    assert [i["name"] for i in _history(admin, since="2026-02-01T00:00:00Z")["items"]] == ["after"]


def test_since_converts_an_offset_to_utc_before_comparing():
    # Midnight at +09:00 is 15:00 the previous day in UTC, so a row created
    # that evening UTC is inside the window. Comparing the offset value as if
    # it were UTC would quietly drop it -- and every row in the nine hours
    # around the boundary with it.
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        job_row(db, admin.id, "completed", title="evening", created_at=datetime(2026, 2, 2, 20, 0))
        job_row(db, admin.id, "completed", title="morning", created_at=datetime(2026, 2, 2, 10, 0))

    body = _history(admin, since="2026-02-03T00:00:00+09:00")

    assert [i["name"] for i in body["items"]] == ["evening"]


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

    body = _history(admin, limit=2)

    assert len(body["items"]) == 2
    assert body["total"] == 5


def test_offset_walks_the_list():
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        for i in range(4):
            job_row(db, admin.id, "completed", title=f"j{i}", created_at=datetime(2026, 1, i + 1))

    page = _history(admin, limit=2, offset=2)

    # Newest first: j3, j2 | j1, j0.
    assert [i["name"] for i in page["items"]] == ["j1", "j0"]
    assert page["offset"] == 2
    assert page["limit"] == 2


def test_an_offset_past_the_end_is_an_empty_page_not_an_error():
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        job_row(db, admin.id, "completed", title="only")

    body = _history(admin, offset=50)

    assert body["items"] == []
    assert body["total"] == 1


def test_pagination_defaults_are_reported():
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        job_row(db, admin.id, "completed", title="only")

    body = _history(admin)

    assert body["limit"] == 100
    assert body["offset"] == 0


def test_an_outsized_limit_is_clamped():
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        job_row(db, admin.id, "completed", title="only")

    assert _history(admin, limit=10000)["limit"] == 500


def test_a_nonsense_limit_or_offset_is_clamped_not_obeyed():
    # A zero limit would render an empty page forever and a negative offset
    # would slice from the end of the list, showing the oldest rows first.
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        for i in range(3):
            job_row(db, admin.id, "completed", title=f"j{i}", created_at=datetime(2026, 1, i + 1))

    zero = _history(admin, limit=0)
    negative_limit = _history(admin, limit=-5)
    negative_offset = _history(admin, offset=-2)

    assert zero["limit"] == 1 and [i["name"] for i in zero["items"]] == ["j2"]
    assert negative_limit["limit"] == 1 and [i["name"] for i in negative_limit["items"]] == ["j2"]
    assert negative_offset["offset"] == 0
    assert [i["name"] for i in negative_offset["items"]] == ["j2", "j1", "j0"]


def test_rows_sharing_a_timestamp_have_a_stable_order():
    # Jobs are created in bursts, so identical timestamps are ordinary. With
    # no tie-break the order is whatever the query happened to return, and two
    # paginated requests can duplicate one row while skipping another.
    admin = make_user(is_admin=True)
    same_moment = datetime(2026, 1, 1, 9, 0)
    with SessionLocal() as db:
        ids = [job_row(db, admin.id, "completed", title=f"j{i}", created_at=same_moment).id
               for i in range(5)]

    body = _history(admin)
    paged = [_history(admin, limit=1, offset=n)["items"][0]["id"] for n in range(5)]

    assert [i["id"] for i in body["items"]] == sorted(ids, reverse=True)
    assert paged == sorted(ids, reverse=True)


# --- rows nothing else would show ------------------------------------------


def test_jobs_and_runs_are_interleaved_by_creation_time():
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        job_row(db, admin.id, "completed", title="newest", created_at=datetime(2026, 1, 3))
        job_row(db, admin.id, "completed", title="oldest", created_at=datetime(2026, 1, 1))
        run_row(db, admin.id, "completed", name="middle", created_at=datetime(2026, 1, 2))

    items = _history(admin)["items"]

    assert [i["name"] for i in items] == ["newest", "middle", "oldest"]


def test_a_job_whose_owner_row_is_gone_is_still_listed():
    admin = make_user(is_admin=True)
    other = make_user(display_name="Ada Lovelace")
    with SessionLocal() as db:
        job_row(db, other.id, "completed", title="orphaned fold")
        db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": other.id})
        db.commit()

    items = _history(admin)["items"]

    assert [(i["name"], i["owner"]) for i in items] == [("orphaned fold", "(unknown)")]


def test_a_run_whose_owner_row_is_gone_is_still_listed():
    admin = make_user(is_admin=True)
    other = make_user(display_name="Ada Lovelace")
    with SessionLocal() as db:
        run_row(db, other.id, "completed", name="orphaned workflow")
        db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": other.id})
        db.commit()

    items = _history(admin)["items"]

    assert [(i["name"], i["owner"]) for i in items] == [("orphaned workflow", "(unknown)")]


def test_an_owner_less_row_does_not_break_the_user_filter():
    # The filter reads names off the owner object, which is None here.
    admin = make_user(is_admin=True)
    other = make_user(display_name="Ada Lovelace")
    with SessionLocal() as db:
        job_row(db, other.id, "completed", title="orphaned fold")
        db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": other.id})
        db.commit()

    assert _history(admin, user="ada")["items"] == []


def test_a_run_whose_workflow_row_is_gone_still_names_itself():
    admin = make_user(is_admin=True)
    other = make_user(display_name="Ada Lovelace")
    with SessionLocal() as db:
        workflow_id = run_row(db, other.id, "completed", name="doomed").workflow_id
        db.execute(text("DELETE FROM workflows WHERE id = :wid"), {"wid": workflow_id})
        db.commit()

    items = _history(admin)["items"]

    assert [i["name"] for i in items] == ["(deleted workflow)"]
    assert items[0]["pipeline"] is None
    assert items[0]["owner"] == "Ada Lovelace"


def test_a_job_without_a_title_falls_back_to_its_pipeline():
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        job_row(db, admin.id, "completed", title="")

    items = _history(admin)["items"]

    assert [i["name"] for i in items] == ["alphafold"]
