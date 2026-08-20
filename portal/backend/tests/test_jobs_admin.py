from datetime import datetime

import pytest
from fastapi import HTTPException

from app import models
from app.database import Base, engine, SessionLocal
from app.routers import jobs as J


def _job(db, user_id, title="t"):
    job = models.Job(
        title=title, pipeline="alphafold", status="completed",
        user_id=user_id,
        created_at=datetime(2026, 1, 1), updated_at=datetime(2026, 1, 1),
    )
    db.add(job); db.commit(); db.refresh(job)
    return job


def test_non_admin_only_sees_their_own_jobs():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        owner = models.User(username="jobs_admin_owner1", password_hash="x"); db.add(owner)
        other = models.User(username="jobs_admin_other1", password_hash="x"); db.add(other)
        db.commit()
        _job(db, owner.id)
        _job(db, other.id)

        owner.is_admin = False
        out = J.list_jobs(db=db, current_user=owner)
        assert {job.user_id for job in db.query(models.Job).filter(models.Job.user_id == owner.id)} == {owner.id}
        assert len(out) == 1


def test_admin_home_list_shows_only_their_own_jobs():
    # Cross-user listing moved to /api/admin/*; the home list is personal for
    # everyone, admin included.
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        admin = models.User(username="jobs_admin_admin1", password_hash="x"); db.add(admin)
        other = models.User(username="jobs_admin_other2", password_hash="x"); db.add(other)
        db.commit()
        _job(db, admin.id, title="mine")
        _job(db, other.id, title="theirs")

        admin.is_admin = True
        out = J.list_jobs(db=db, current_user=admin)
        assert {job.title for job in out} == {"mine"}


def test_home_list_never_sets_the_owner_field():
    # `owner` stays in the schema for the admin views, but the home list only
    # ever contains the caller's own jobs, so it is always None here.
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        admin = models.User(username="jobs_admin_admin2", password_hash="x"); db.add(admin)
        other = models.User(username="jobs_admin_other3", password_hash="x"); db.add(other)
        db.commit()
        _job(db, admin.id, title="mine")
        _job(db, other.id, title="theirs")

        admin.is_admin = True
        out = J.list_jobs(db=db, current_user=admin)
        assert [job.owner for job in out] == [None]


def test_non_admin_cannot_read_someone_elses_job():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        owner = models.User(username="jobs_admin_owner2", password_hash="x"); db.add(owner)
        other = models.User(username="jobs_admin_other4", password_hash="x"); db.add(other)
        db.commit()
        job = _job(db, other.id)

        owner.is_admin = False
        with pytest.raises(HTTPException) as exc_info:
            J._get_job_or_404(db, owner, job.id)
        assert exc_info.value.status_code == 404


def test_admin_can_read_someone_elses_job():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        admin = models.User(username="jobs_admin_admin3", password_hash="x"); db.add(admin)
        other = models.User(username="jobs_admin_other5", password_hash="x"); db.add(other)
        db.commit()
        job = _job(db, other.id)

        admin.is_admin = True
        found = J._get_job_or_404(db, admin, job.id)
        assert found.id == job.id
