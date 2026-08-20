"""An admin must be able to open and cancel any account's workflow run.

The admin activity/history views list every account's runs, so a 404 on a
row the same admin page had just shown would be the wrong answer. This
mirrors the bypass jobs.py's _get_job_or_404 already has.
"""

import pytest
from fastapi import HTTPException

from admin_helpers import client_as, make_user, run_row
from app import models
from app.database import SessionLocal
from app.routers import workflows as W


def test_an_admin_can_open_someone_elses_run():
    admin = make_user(is_admin=True)
    other = make_user()
    with SessionLocal() as db:
        run_id = run_row(db, other.id, "completed", name="their workflow").id

    resp = client_as(admin).get(f"/api/workflows/runs/{run_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == run_id


def test_a_stranger_cannot_open_someone_elses_run():
    stranger = make_user()
    other = make_user()
    with SessionLocal() as db:
        run_id = run_row(db, other.id, "completed", name="their workflow").id

    resp = client_as(stranger).get(f"/api/workflows/runs/{run_id}")
    assert resp.status_code == 404


def test_an_admin_can_open_someone_elses_report():
    admin = make_user(is_admin=True)
    other = make_user()
    with SessionLocal() as db:
        run_id = run_row(db, other.id, "completed", name="their workflow").id

    resp = client_as(admin).get(f"/api/workflows/runs/{run_id}/report")
    assert resp.status_code == 200
    assert resp.json()["run_id"] == run_id


def test_a_stranger_cannot_open_someone_elses_report():
    stranger = make_user()
    other = make_user()
    with SessionLocal() as db:
        run_id = run_row(db, other.id, "completed", name="their workflow").id

    resp = client_as(stranger).get(f"/api/workflows/runs/{run_id}/report")
    assert resp.status_code == 404


def test_an_admin_can_cancel_someone_elses_run():
    admin = make_user(is_admin=True)
    other = make_user()
    with SessionLocal() as db:
        run_id = run_row(db, other.id, "queued", name="their workflow").id

    resp = client_as(admin).post(f"/api/workflows/runs/{run_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


def test_a_stranger_cannot_cancel_someone_elses_run():
    stranger = make_user()
    other = make_user()
    with SessionLocal() as db:
        run_id = run_row(db, other.id, "queued", name="their workflow").id

    resp = client_as(stranger).post(f"/api/workflows/runs/{run_id}/cancel")
    assert resp.status_code == 404


def test_a_user_object_that_never_went_through_auth_is_not_treated_as_admin():
    # A bare models.User() has no is_admin attribute at all (it's not a
    # column, only get_current_user attaches it per-request). _run_or_404
    # must fail closed rather than assume admin for such an object.
    stranger = models.User()
    stranger.id = -1
    with SessionLocal() as db:
        other = make_user()
        run_id = run_row(db, other.id, "completed", name="their workflow").id

        with pytest.raises(HTTPException) as exc_info:
            W._run_or_404(db, stranger, run_id)
        assert exc_info.value.status_code == 404
