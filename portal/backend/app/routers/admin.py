from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models, queue_estimate
from ..auth import get_current_user
from ..database import get_db
from ..user_display import display_name_for

router = APIRouter(prefix="/api/admin", tags=["admin"])

# WorkflowRun's own vocabulary; queued/running are the only non-terminal ones.
# Jobs use a different, wider set -- queue_estimate.ACTIVE_STATUSES.
ACTIVE_RUN_STATUSES = {"queued", "running"}
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def _seconds_between(start: datetime | None, end: datetime | None) -> int | None:
    if not start or not end:
        return None
    return max(0, int((end - start).total_seconds()))


def require_admin(current_user: models.User = Depends(get_current_user)) -> models.User:
    """Gate every cross-user view on the Keycloak `kbf-admin` realm role.

    `is_admin` is set by auth.get_current_user from the X-KBF-Admin header,
    which the backend only trusts once the gateway shared secret checks out
    -- except in the dev/test opt-in (KBF_ALLOW_INSECURE_SSO_HEADER with no
    secret configured), where the header is trusted with no secret at all.
    See auth.get_current_user for the exact trust boundary.
    """
    if not getattr(current_user, "is_admin", False):
        raise HTTPException(status_code=403, detail="admin only")
    return current_user


@router.get("/me")
def me(current_user: models.User = Depends(get_current_user)) -> dict:
    """Deliberately open to every authenticated user (not just admins): the
    frontend asks this to decide whether to render the admin menu at all. An
    unauthenticated request still gets 401 from get_current_user."""
    return {"is_admin": bool(getattr(current_user, "is_admin", False))}


@router.get("/activity")
def activity(db: Session = Depends(get_db), _: models.User = Depends(require_admin)) -> dict:
    """Everything currently queued or running, across every account."""
    now = datetime.utcnow()
    averages = queue_estimate.average_durations(db)
    items: list[dict] = []

    # WorkflowRun has no `owner` relationship and the test users are detached,
    # so both halves join models.User explicitly instead of traversing.
    job_rows = (
        db.query(models.Job, models.User)
        .join(models.User, models.User.id == models.Job.user_id)
        .filter(func.lower(models.Job.status).in_(queue_estimate.ACTIVE_STATUSES))
        .order_by(models.Job.created_at.desc())
        .all()
    )
    for job, owner in job_rows:
        estimate = queue_estimate.estimate_for_job(db, job, averages, now) or {}
        # Not estimate.get(key, fallback): a present-but-None value would slip
        # through that and surface as a null elapsed time in the UI.
        elapsed = estimate.get("elapsed_seconds")
        if elapsed is None:
            elapsed = _seconds_between(job.created_at, now)
        items.append({
            "kind": "job",
            "id": job.id,
            "owner": display_name_for(owner),
            "name": job.title or job.pipeline,
            "pipeline": job.pipeline,
            "status": job.status,
            "started_at": job.created_at.isoformat() if job.created_at else None,
            "elapsed_seconds": elapsed,
            "queue_position": estimate.get("queue_position"),
            "eta_seconds": estimate.get("eta_seconds"),
        })

    run_rows = (
        db.query(models.WorkflowRun, models.User, models.Workflow)
        .join(models.User, models.User.id == models.WorkflowRun.owner_id)
        .join(models.Workflow, models.Workflow.id == models.WorkflowRun.workflow_id)
        .filter(models.WorkflowRun.status.in_(ACTIVE_RUN_STATUSES))
        .order_by(models.WorkflowRun.created_at.desc())
        .all()
    )
    for run, owner, workflow in run_rows:
        started = run.started_at or run.created_at
        items.append({
            "kind": "workflow",
            "id": run.id,
            "owner": display_name_for(owner),
            "name": workflow.name,
            "pipeline": workflow.template_key,
            "status": run.status,
            "started_at": started.isoformat() if started else None,
            # A multi-step run has no single endpoint queue, so no position/ETA.
            "elapsed_seconds": _seconds_between(started, now),
            "queue_position": None,
            "eta_seconds": None,
        })

    # Every started_at here is datetime.isoformat() on a naive UTC value, so
    # lexicographic order is chronological order; unknown starts sort last.
    items.sort(key=lambda item: item["started_at"] or "", reverse=True)
    return {"items": items}
