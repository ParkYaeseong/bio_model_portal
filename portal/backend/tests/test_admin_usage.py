"""GET /api/admin/usage -- per-account totals, busiest first.

The page answers "who is consuming the fleet", so every number in it gets a
case that fails if it is counted into the wrong bucket, summed from the wrong
rows or ordered the wrong way round. The grouping key gets its own cases:
folding two accounts together is the one error this view must never make.
"""

from datetime import datetime

from sqlalchemy import text

from admin_helpers import client_as, job_row, make_user, run_row
from app.database import SessionLocal

# Every key an entry is expected to publish. Spelled out rather than derived
# from the code, so a private key that leaked into the aggregate (an owner
# object, a raw datetime) has to fail here.
PUBLIC_KEYS = {
    "owner_id", "owner", "total", "completed", "failed", "cancelled",
    "active", "total_seconds", "last_activity",
}


def _usage(admin, **params) -> list[dict]:
    """The `users` list of one successful /api/admin/usage call."""
    response = client_as(admin).get("/api/admin/usage", params=params)
    assert response.status_code == 200, response.text
    return response.json()["users"]


def _for(users, owner_id) -> dict:
    """The one entry belonging to `owner_id`."""
    matches = [entry for entry in users if entry["owner_id"] == owner_id]
    assert len(matches) == 1, f"expected exactly one entry for {owner_id}: {users}"
    return matches[0]


def _set(db, table, row_id, **values):
    """Set columns the admin_helpers factories do not expose (finished_at).

    test-only: table and column names are literals here, never request data.
    """
    assignments = ", ".join(f"{column} = :{column}" for column in values)
    db.execute(
        text(f"UPDATE {table} SET {assignments} WHERE id = :row_id"),
        {**values, "row_id": row_id},
    )
    db.commit()


# --- the shape of an entry -------------------------------------------------


def test_usage_reports_an_account_in_full():
    admin = make_user(is_admin=True)
    ada = make_user(display_name="Ada Lovelace")
    with SessionLocal() as db:
        job_row(db, ada.id, "completed", created_at=datetime(2026, 1, 5, 9, 0),
                updated_at=datetime(2026, 1, 5, 9, 30))
        job_row(db, ada.id, "running", created_at=datetime(2026, 1, 6, 9, 0),
                updated_at=datetime(2026, 1, 6, 12, 0))

    assert _for(_usage(admin), ada.id) == {
        "owner_id": ada.id,
        "owner": "Ada Lovelace",
        "total": 2,
        "completed": 1,
        "failed": 0,
        "cancelled": 0,
        "active": 1,
        "total_seconds": 1800,
        "last_activity": "2026-01-06T09:00:00",
    }


def test_usage_publishes_exactly_the_public_keys():
    # The rows this aggregates carry an ORM object and a raw datetime; neither
    # may reach the client, and neither would even serialise.
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        job_row(db, admin.id, "completed")

    assert set(_usage(admin)[0]) == PUBLIC_KEYS


def test_an_account_that_has_run_nothing_is_not_listed():
    admin = make_user(is_admin=True)
    busy = make_user(display_name="Busy")
    make_user(display_name="Idle")
    with SessionLocal() as db:
        job_row(db, busy.id, "completed")

    assert [entry["owner_id"] for entry in _usage(admin)] == [busy.id]


def test_usage_counts_runs_as_well_as_jobs():
    admin = make_user(is_admin=True)
    ada = make_user(display_name="Ada")
    with SessionLocal() as db:
        job_row(db, ada.id, "completed", created_at=datetime(2026, 1, 1))
        run_row(db, ada.id, "completed", created_at=datetime(2026, 1, 2, 9, 0))

    assert _for(_usage(admin), ada.id)["total"] == 2


# --- the outcome buckets ---------------------------------------------------


def test_usage_counts_per_user_outcomes():
    admin = make_user(is_admin=True)
    ada = make_user(display_name="Ada")
    with SessionLocal() as db:
        job_row(db, ada.id, "completed", title="one")
        job_row(db, ada.id, "completed", title="two")
        job_row(db, ada.id, "failed", title="three")
        job_row(db, ada.id, "running", title="four")

    ada_usage = _for(_usage(admin), ada.id)

    assert ada_usage["total"] == 4
    assert ada_usage["completed"] == 2
    assert ada_usage["failed"] == 1
    assert ada_usage["active"] == 1
    assert ada_usage["cancelled"] == 0


def test_each_outcome_lands_in_its_own_bucket():
    # Deliberately four different counts: with any two buckets swapped, or any
    # one of them fed from the wrong status, the numbers below stop matching.
    admin = make_user(is_admin=True)
    ada = make_user(display_name="Ada")
    with SessionLocal() as db:
        job_row(db, ada.id, "completed", title="c1")
        for n in range(2):
            job_row(db, ada.id, "failed", title=f"f{n}")
        for n in range(3):
            job_row(db, ada.id, "cancelled", title=f"x{n}")
        job_row(db, ada.id, "running", title="a1")
        job_row(db, ada.id, "pending", title="a2")
        job_row(db, ada.id, "queued", title="a3")
        run_row(db, ada.id, "queued", name="a4")

    ada_usage = _for(_usage(admin), ada.id)

    assert (ada_usage["completed"], ada_usage["failed"],
            ada_usage["cancelled"], ada_usage["active"]) == (1, 2, 3, 4)
    assert ada_usage["total"] == 10


def test_a_status_nobody_anticipated_counts_as_active():
    # The same bias as /activity: an unrecognised worker status shows up on
    # the admin's page as unfinished work rather than dropping out of the
    # totals, which would make them quietly not add up.
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        job_row(db, admin.id, "reticulating_splines")

    entry = _for(_usage(admin), admin.id)

    assert (entry["total"], entry["active"]) == (1, 1)
    assert (entry["completed"], entry["failed"], entry["cancelled"]) == (0, 0, 0)


def test_a_row_with_no_status_counts_as_active(monkeypatch):
    # status is NOT NULL, so this row cannot be built through the factories
    # either -- but a bucket lookup that assumed otherwise would 500 the whole
    # page over one bad row, and an unknown state is unfinished work.
    from app.routers import admin as admin_router

    admin = make_user(is_admin=True)
    monkeypatch.setattr(admin_router, "_history_rows", lambda db: [{
        "kind": "job", "id": "j1", "owner_id": 7, "owner": "Ghost",
        "name": "n", "pipeline": "alphafold", "status": None,
        "created_at": "2026-01-01T00:00:00", "duration_seconds": None,
        "error_message": None, "_owner": None, "_created": datetime(2026, 1, 1),
    }])

    entry = _for(_usage(admin), 7)

    assert (entry["total"], entry["active"]) == (1, 1)


def test_the_buckets_ignore_the_case_a_worker_shouted_in():
    admin = make_user(is_admin=True)
    ada = make_user(display_name="Ada")
    with SessionLocal() as db:
        job_row(db, ada.id, "COMPLETED", title="c")
        job_row(db, ada.id, "Failed", title="f")
        job_row(db, ada.id, "CanCelled", title="x")

    entry = _for(_usage(admin), ada.id)

    assert (entry["completed"], entry["failed"], entry["cancelled"]) == (1, 1, 1)
    assert entry["active"] == 0


# --- summed run time -------------------------------------------------------


def test_usage_sums_only_finished_durations():
    # The running job has been touched by the poller five hours after it
    # started; that is "when we last heard", not five hours of run time.
    admin = make_user(is_admin=True)
    grace = make_user(display_name="Grace")
    with SessionLocal() as db:
        job_row(db, grace.id, "completed", title="done",
                created_at=datetime(2026, 1, 1, 9, 0), updated_at=datetime(2026, 1, 1, 9, 1))
        job_row(db, grace.id, "running", title="in flight",
                created_at=datetime(2026, 1, 1, 9, 0), updated_at=datetime(2026, 1, 1, 14, 0))

    grace_usage = _for(_usage(admin), grace.id)

    assert grace_usage["total"] == 2
    assert grace_usage["total_seconds"] == 60


def test_unmeasurable_work_totals_zero_seconds_not_null():
    # A queued run and a job still in flight can each be counted but not
    # timed. The account still has to report a number.
    admin = make_user(is_admin=True)
    ada = make_user(display_name="Ada")
    with SessionLocal() as db:
        job_row(db, ada.id, "running")
        run_row(db, ada.id, "queued")

    entry = _for(_usage(admin), ada.id)

    assert entry["total"] == 2
    assert entry["total_seconds"] == 0


def test_usage_adds_up_both_halves_of_the_union():
    admin = make_user(is_admin=True)
    ada = make_user(display_name="Ada")
    with SessionLocal() as db:
        job_row(db, ada.id, "completed", created_at=datetime(2026, 1, 1, 9, 0),
                updated_at=datetime(2026, 1, 1, 9, 30))
        run = run_row(db, ada.id, "completed", created_at=datetime(2026, 1, 2, 9, 0),
                      started_at=datetime(2026, 1, 2, 10, 0))
        _set(db, "workflow_runs", run.id, finished_at=datetime(2026, 1, 2, 10, 5))

    assert _for(_usage(admin), ada.id)["total_seconds"] == 1800 + 300


# --- last activity ---------------------------------------------------------


def test_last_activity_is_the_newest_row_not_the_oldest():
    admin = make_user(is_admin=True)
    ada = make_user(display_name="Ada")
    with SessionLocal() as db:
        job_row(db, ada.id, "completed", title="old", created_at=datetime(2026, 1, 1, 9, 0))
        job_row(db, ada.id, "completed", title="new", created_at=datetime(2026, 3, 4, 17, 45))
        job_row(db, ada.id, "completed", title="middle", created_at=datetime(2026, 2, 1, 9, 0))

    assert _for(_usage(admin), ada.id)["last_activity"] == "2026-03-04T17:45:00"


def test_last_activity_is_per_account_not_fleet_wide():
    admin = make_user(is_admin=True)
    ada = make_user(display_name="Ada")
    with SessionLocal() as db:
        job_row(db, ada.id, "completed", created_at=datetime(2026, 1, 1, 9, 0))
        job_row(db, admin.id, "completed", created_at=datetime(2026, 5, 5, 9, 0))

    users = _usage(admin)

    assert _for(users, ada.id)["last_activity"] == "2026-01-01T09:00:00"
    assert _for(users, admin.id)["last_activity"] == "2026-05-05T09:00:00"


def test_last_activity_is_null_when_no_row_has_a_creation_time(monkeypatch):
    # created_at is NOT NULL, so this row cannot be built through the factories
    # -- but the aggregate reads a nullable-in-principle column and must answer
    # null rather than raise on it, which is the same defence _history_rows
    # takes. Fed in directly for that reason.
    from app.routers import admin as admin_router

    admin = make_user(is_admin=True)
    monkeypatch.setattr(admin_router, "_history_rows", lambda db: [{
        "kind": "job", "id": "j1", "owner_id": 7, "owner": "Ghost",
        "name": "n", "pipeline": "alphafold", "status": "completed",
        "created_at": None, "duration_seconds": None, "error_message": None,
        "_owner": None, "_created": None,
    }])

    entry = _for(_usage(admin), 7)

    assert entry["last_activity"] is None
    assert entry["total"] == 1


def test_last_activity_is_a_string_before_it_is_serialised():
    # Read off the handler rather than through the client, because FastAPI
    # encodes a datetime to the same text an isoformat() call produces: over
    # HTTP, forgetting to render it is invisible. The handler's own contract is
    # a string, and a caller that is not the JSON encoder depends on it.
    from app.routers import admin as admin_router

    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        job_row(db, admin.id, "completed", created_at=datetime(2026, 3, 4, 17, 45))
        entry = admin_router.usage(since=None, db=db, _=admin)["users"][0]

    assert entry["last_activity"] == "2026-03-04T17:45:00"
    assert isinstance(entry["last_activity"], str)


# --- grouping --------------------------------------------------------------


def test_two_deleted_accounts_are_not_folded_into_one():
    # Both render as "(unknown)", so grouping on the display label would
    # report two people's fleet usage as one person's.
    admin = make_user(is_admin=True)
    one = make_user(display_name="Ada Lovelace")
    two = make_user(display_name="Grace Hopper")
    with SessionLocal() as db:
        job_row(db, one.id, "completed", title="hers", created_at=datetime(2026, 1, 2))
        job_row(db, two.id, "failed", title="theirs", created_at=datetime(2026, 1, 1))
        db.execute(text("DELETE FROM users WHERE id IN (:a, :b)"), {"a": one.id, "b": two.id})
        db.commit()

    users = _usage(admin)

    assert {entry["owner"] for entry in users} == {"(unknown)"}
    assert _for(users, one.id)["completed"] == 1
    assert _for(users, two.id)["failed"] == 1
    assert [entry["total"] for entry in users] == [1, 1]


def test_two_live_accounts_sharing_a_name_are_not_folded_into_one():
    admin = make_user(is_admin=True)
    one = make_user(display_name="Chris Green")
    two = make_user(display_name="Chris Green")
    with SessionLocal() as db:
        job_row(db, one.id, "completed", title="hers")
        job_row(db, two.id, "completed", title="theirs")

    users = _usage(admin)

    assert _for(users, one.id)["total"] == 1
    assert _for(users, two.id)["total"] == 1


def test_every_row_of_one_account_lands_in_one_entry():
    admin = make_user(is_admin=True)
    ada = make_user(display_name="Ada")
    with SessionLocal() as db:
        for n in range(4):
            job_row(db, ada.id, "completed", title=f"j{n}")
        run_row(db, ada.id, "completed")

    assert len([e for e in _usage(admin) if e["owner_id"] == ada.id]) == 1


# --- ordering --------------------------------------------------------------


def test_usage_is_sorted_by_volume():
    admin = make_user(is_admin=True)
    quiet = make_user(display_name="Quiet")
    busy = make_user(display_name="Busy")
    with SessionLocal() as db:
        job_row(db, quiet.id, "completed", title="only", created_at=datetime(2026, 3, 1))
        for n in range(3):
            job_row(db, busy.id, "completed", title=f"b{n}", created_at=datetime(2026, 1, n + 1))

    owners = [entry["owner"] for entry in _usage(admin)]

    assert owners.index("Busy") < owners.index("Quiet")


def test_equal_totals_hold_a_fixed_order():
    # Without a tie-break the order is whatever the newest-first scan produced,
    # so two equally busy accounts swap places as soon as one of them runs
    # something. Zeta's row is the newer one, so a missing tie-break puts it
    # first; the label decides instead.
    admin = make_user(is_admin=True)
    alpha = make_user(display_name="Alpha")
    zeta = make_user(display_name="Zeta")
    with SessionLocal() as db:
        job_row(db, alpha.id, "completed", title="a", created_at=datetime(2026, 1, 1))
        job_row(db, zeta.id, "completed", title="z", created_at=datetime(2026, 2, 1))

    assert [entry["owner"] for entry in _usage(admin)] == ["Alpha", "Zeta"]


def test_the_tie_break_reads_names_the_way_a_reader_does():
    # Case-insensitively: display names come from an identity provider in
    # whatever case their owner typed, and ASCII order would file every
    # lowercase name after every capitalised one, which reads as unsorted.
    admin = make_user(is_admin=True)
    ada = make_user(display_name="ada")
    zeta = make_user(display_name="Zeta")
    with SessionLocal() as db:
        job_row(db, ada.id, "completed", title="a", created_at=datetime(2026, 1, 1))
        job_row(db, zeta.id, "completed", title="z", created_at=datetime(2026, 2, 1))

    assert [entry["owner"] for entry in _usage(admin)] == ["ada", "Zeta"]


def test_equal_totals_under_one_name_break_on_the_account_id():
    # Same label, same total: only the id can separate them, and it has to,
    # or the two rows trade places between requests. The newer row belongs to
    # the later id, so a missing id tie-break reverses this list.
    admin = make_user(is_admin=True)
    first = make_user(display_name="Chris Green")
    second = make_user(display_name="Chris Green")
    assert str(first.id) < str(second.id), "test assumes ascending, string-ordered ids"
    with SessionLocal() as db:
        job_row(db, first.id, "completed", title="a", created_at=datetime(2026, 1, 1))
        job_row(db, second.id, "completed", title="b", created_at=datetime(2026, 2, 1))

    assert [entry["owner_id"] for entry in _usage(admin)] == [first.id, second.id]


def test_the_busiest_account_is_first_even_when_it_last_ran_long_ago():
    # Volume, not recency: the scan hands rows over newest-first, so an
    # ordering that never re-sorted would put the recent, quieter account top.
    admin = make_user(is_admin=True)
    busy = make_user(display_name="Busy")
    recent = make_user(display_name="Recent")
    with SessionLocal() as db:
        for n in range(5):
            job_row(db, busy.id, "completed", title=f"b{n}", created_at=datetime(2026, 1, n + 1))
        job_row(db, recent.id, "completed", title="r", created_at=datetime(2026, 9, 9))

    assert [entry["owner"] for entry in _usage(admin)] == ["Busy", "Recent"]


# --- the since filter ------------------------------------------------------


def test_since_narrows_the_totals():
    admin = make_user(is_admin=True)
    ada = make_user(display_name="Ada")
    with SessionLocal() as db:
        job_row(db, ada.id, "completed", title="after", created_at=datetime(2026, 2, 2))
        job_row(db, ada.id, "completed", title="boundary", created_at=datetime(2026, 2, 1))
        job_row(db, ada.id, "failed", title="before", created_at=datetime(2026, 1, 31))

    entry = _for(_usage(admin, since="2026-02-01T00:00:00"), ada.id)

    assert entry["total"] == 2
    assert entry["completed"] == 2
    assert entry["failed"] == 0
    assert entry["last_activity"] == "2026-02-02T00:00:00"


def test_since_can_empty_the_page():
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        job_row(db, admin.id, "completed", created_at=datetime(2026, 1, 1))

    assert _usage(admin, since="2026-06-01") == []


def test_since_reads_the_same_here_as_on_history():
    # Both endpoints route the value through _parse_since, so an offset is
    # converted to UTC before it is compared rather than taken as UTC: midnight
    # at +09:00 is 15:00 the previous day, which leaves the evening row inside
    # the window and the morning one outside it.
    admin = make_user(is_admin=True)
    ada = make_user(display_name="Ada")
    with SessionLocal() as db:
        job_row(db, ada.id, "completed", title="evening", created_at=datetime(2026, 2, 2, 20, 0))
        job_row(db, ada.id, "completed", title="morning", created_at=datetime(2026, 2, 2, 10, 0))

    assert _for(_usage(admin, since="2026-02-03T00:00:00+09:00"), ada.id)["total"] == 1


def test_a_blank_since_filters_nothing():
    admin = make_user(is_admin=True)
    with SessionLocal() as db:
        job_row(db, admin.id, "completed", created_at=datetime(2026, 1, 1))
        job_row(db, admin.id, "failed", created_at=datetime(2020, 1, 1))

    assert _for(_usage(admin, since=""), admin.id)["total"] == 2
    assert _for(_usage(admin, since="   "), admin.id)["total"] == 2


def test_usage_rejects_a_since_it_cannot_parse():
    # A typo in a filter box is a bad request, not a crash -- and it answers
    # exactly as /history does.
    admin = make_user(is_admin=True)

    response = client_as(admin).get("/api/admin/usage", params={"since": "last tuesday"})

    assert response.status_code == 400
    assert "since" in response.json()["detail"]


# --- access ----------------------------------------------------------------


def test_usage_is_admin_only():
    nobody = make_user(is_admin=False)

    assert client_as(nobody).get("/api/admin/usage").status_code == 403
