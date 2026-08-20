"""Shared helpers for the admin API tests."""

import uuid
from datetime import datetime

from fastapi.testclient import TestClient

from app import models
from app.auth import get_current_user
from app.database import SessionLocal
from app.main import app


def make_user(is_admin=False, **kwargs):
    """A persisted account, detached so it can stand in for the SSO identity.

    `is_admin` is not a column: auth.get_current_user attaches it per request
    from the X-KBF-Admin header, so tests set it the same way.

    Note: the returned object is detached from its session (expunged), so
    plain attribute access works but relationship access (e.g. `user.jobs`)
    raises DetachedInstanceError. Handlers exercised against this object must
    query for related rows rather than traverse relationships off it.
    """
    with SessionLocal() as db:
        user = models.User(username=f"adm_{uuid.uuid4().hex[:8]}", password_hash="x", **kwargs)
        db.add(user); db.commit(); db.refresh(user)
        db.expunge(user)
    user.is_admin = is_admin
    return user


def client_as(user):
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def job_row(db, user_id, status, title="t", endpoint_id="ep-1", created_at=None, updated_at=None):
    job = models.Job(
        title=title, pipeline="alphafold", status=status, endpoint_id=endpoint_id,
        user_id=user_id,
        created_at=created_at or datetime(2026, 1, 1),
        updated_at=updated_at or datetime(2026, 1, 1),
        expires_at=datetime(2027, 1, 1),
    )
    db.add(job); db.commit(); db.refresh(job)
    return job


def run_row(db, owner_id, status, name="wf"):
    workflow = models.Workflow(name=name, owner_id=owner_id, template_key="rapid_v1")
    db.add(workflow); db.commit(); db.refresh(workflow)
    run = models.WorkflowRun(
        workflow_id=workflow.id, owner_id=owner_id, status=status,
        created_at=datetime(2026, 1, 2), started_at=datetime(2026, 1, 2),
    )
    db.add(run); db.commit(); db.refresh(run)
    return run
