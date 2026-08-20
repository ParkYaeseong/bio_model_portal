from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models, queue_estimate
from ..auth import get_current_user
from ..database import get_db
from ..user_display import display_name_for

router = APIRouter(prefix="/api/admin", tags=["admin"])

# The two halves of this page filter in opposite directions, on purpose.
# A job's status is whatever an external worker reported, so it is matched
# against queue_estimate's allowlist -- the same set the ETA math keys off, so
# the two can never disagree. A run's status is a literal this codebase writes
# itself, so the filter is a denylist of the finished ones: a status nobody
# anticipated then shows up looking odd on the admin's page instead of
# silently vanishing from it.
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def _seconds_between(start: datetime | None, end: datetime | None) -> int | None:
    if not start or not end:
        return None
    return max(0, int((end - start).total_seconds()))


def _job_rows(db: Session, *, statuses: set[str] | None = None) -> list[tuple]:
    """(Job, owner) rows, newest first. `statuses` is a lowercase allowlist.

    outerjoin, not join: on a page whose whole purpose is to hide nothing, an
    inner join would drop a job whose owner row had gone missing. Nothing
    deletes users today, but PRAGMA foreign_keys is 0 on this database so
    nothing enforces that either -- and display_name_for already renders a
    null owner as "(unknown)". WorkflowRun has no `owner` relationship anyway,
    and admin tests hold detached users, so both halves join explicitly rather
    than traverse.
    """
    query = db.query(models.Job, models.User).outerjoin(
        models.User, models.User.id == models.Job.user_id
    )
    if statuses is not None:
        # Worker-reported text: compare case-insensitively.
        query = query.filter(func.lower(models.Job.status).in_(statuses))
    return query.order_by(models.Job.created_at.desc()).all()


def _run_rows(db: Session, *, exclude_statuses: set[str] | None = None) -> list[tuple]:
    """(WorkflowRun, owner, workflow) rows, newest first.

    Outer-joined for the same reason as _job_rows; a run whose workflow row is
    gone still names itself rather than disappearing.
    """
    query = (
        db.query(models.WorkflowRun, models.User, models.Workflow)
        .outerjoin(models.User, models.User.id == models.WorkflowRun.owner_id)
        .outerjoin(models.Workflow, models.Workflow.id == models.WorkflowRun.workflow_id)
    )
    if exclude_statuses is not None:
        query = query.filter(~models.WorkflowRun.status.in_(exclude_statuses))
    return query.order_by(models.WorkflowRun.created_at.desc()).all()


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
    items: list[dict] = []

    job_rows = _job_rows(db, statuses=queue_estimate.ACTIVE_STATUSES)
    # average_durations scans 200 rows; skip it when there is nothing to
    # estimate. An idle fleet is the common case and this page polls often.
    averages = queue_estimate.average_durations(db) if job_rows else {}
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

    for run, owner, workflow in _run_rows(db, exclude_statuses=TERMINAL_STATUSES):
        # started_at is only set when a run actually starts, so every queued
        # run falls back to when it was created.
        started = run.started_at or run.created_at
        items.append({
            "kind": "workflow",
            "id": run.id,
            "owner": display_name_for(owner),
            "name": workflow.name if workflow else "(deleted workflow)",
            "pipeline": workflow.template_key if workflow else None,
            "status": run.status,
            "started_at": started.isoformat() if started else None,
            # A multi-step run has no single endpoint queue, so no position/ETA.
            "elapsed_seconds": _seconds_between(started, now),
            "queue_position": None,
            "eta_seconds": None,
        })

    # Every started_at here is datetime.isoformat() on a naive UTC value, so
    # lexicographic order is chronological order; unknown starts sort last.
    # Both queries already return newest-first, which this stable sort keeps
    # only as the tie-break between rows sharing a timestamp.
    items.sort(key=lambda item: item["started_at"] or "", reverse=True)
    return {"items": items}


# A page an admin scrolls, not an export: 100 rows fills a screen and 500 is
# the most the merged-in-Python list below is worth building in one response.
DEFAULT_HISTORY_LIMIT = 100
MAX_HISTORY_LIMIT = 500


def _parse_since(value: str | None) -> datetime | None:
    """`since` as a naive UTC datetime, or None when it was not supplied.

    Naive because every timestamp in this database is naive UTC, so an aware
    value has to be converted before it can be compared to one. A value that
    does not parse is the admin's typo, not a server fault: 400, not 500.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        # `from None`: the parse error adds nothing the message does not say.
        raise HTTPException(
            status_code=400, detail=f"since: not an ISO 8601 datetime: {value!r}"
        ) from None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _history_rows(db: Session) -> list[dict]:
    """Every job and every run ever, newest first, as one merged list.

    Merged in Python rather than in SQL: the two tables share no column shape
    worth unioning, the whole dataset is a couple of dozen rows, and the per-
    row shaping (deleted-workflow names, the two different duration rules)
    would be unreadable as SQL. Kept separate from the endpoint because the
    usage view aggregates these same rows per user.

    Rows carry two underscore-prefixed keys the client never sees: `_owner`,
    the account object the user filter reads every name off, and `_created`,
    the raw datetime the `since` filter compares against. The endpoint strips
    them -- a SQLAlchemy object would not serialise at all.
    """
    rows: list[dict] = []

    for job, owner in _job_rows(db):
        # created_at -> updated_at, but only once the job has stopped moving:
        # on a job still running, updated_at is when the poller last heard
        # from the worker, which is not an end time.
        finished = (job.status or "").lower() in TERMINAL_STATUSES
        rows.append({
            "kind": "job",
            "id": job.id,
            "owner": display_name_for(owner),
            "name": job.title or job.pipeline,
            "pipeline": job.pipeline,
            "status": job.status,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "duration_seconds": _seconds_between(job.created_at, job.updated_at) if finished else None,
            "error_message": job.error_message,
            "_owner": owner,
            "_created": job.created_at,
        })

    for run, owner, workflow in _run_rows(db):
        rows.append({
            "kind": "workflow",
            "id": run.id,
            "owner": display_name_for(owner),
            "name": workflow.name if workflow else "(deleted workflow)",
            "pipeline": workflow.template_key if workflow else None,
            "status": run.status,
            # created_at, so both halves sort and filter on the same thing:
            # when the work was asked for.
            "created_at": run.created_at.isoformat() if run.created_at else None,
            # No fallback to created_at here (unlike /activity's elapsed time):
            # a run that never started spent no time running, and calling its
            # age a duration would inflate every per-user total built on these
            # rows. Either endpoint missing means "not measurable".
            "duration_seconds": _seconds_between(run.started_at, run.finished_at),
            "error_message": run.error_message,
            "_owner": owner,
            "_created": run.created_at,
        })

    # Same ordering argument as /activity: naive-UTC isoformat sorts
    # lexicographically in chronological order, and an unknown creation time
    # sorts last rather than crashing the comparison.
    rows.sort(key=lambda row: row["created_at"] or "", reverse=True)
    return rows


def _owner_haystack(owner: models.User | None) -> str:
    """Every name an account answers to, lowercased, for substring matching.

    All three, not just the username: SSO usernames are OIDC subjects, so an
    admin searching for a colleague types their display name or their email.
    """
    if owner is None:
        return ""
    return " ".join(
        (value or "") for value in (owner.username, owner.email, owner.display_name)
    ).lower()


def _history_matches(
    row: dict,
    *,
    user: str | None,
    pipeline: str | None,
    status: str | None,
    since: datetime | None,
) -> bool:
    """Every filter is optional and an empty value means "do not filter": the
    UI submits its whole filter bar on every request, blank boxes included."""
    if user and user.strip().lower() not in _owner_haystack(row["_owner"]):
        return False
    # Exact, not substring: 'alphafold' must not drag in 'alphafold_multimer'.
    # Case-insensitive because both columns hold text written by workers and
    # by templates, in whatever case they chose.
    if pipeline and (row["pipeline"] or "").lower() != pipeline.strip().lower():
        return False
    if status and (row["status"] or "").lower() != status.strip().lower():
        return False
    if since is not None:
        # A row with no creation time cannot be shown to fall inside the
        # window, so an explicit window excludes it.
        if row["_created"] is None or row["_created"] < since:
            return False
    return True


@router.get("/history")
def history(
    user: str | None = None,
    pipeline: str | None = None,
    status: str | None = None,
    since: str | None = None,
    limit: int = DEFAULT_HISTORY_LIMIT,
    offset: int = 0,
    db: Session = Depends(get_db),
    _: models.User = Depends(require_admin),
) -> dict:
    """Everything ever run, by anyone, newest first, filtered and paginated."""
    since_dt = _parse_since(since)

    matched = [
        row for row in _history_rows(db)
        if _history_matches(row, user=user, pipeline=pipeline, status=status, since=since_dt)
    ]
    # Counted before the slice: the UI's "showing 100 of 4,312" needs the size
    # of the filtered result, not the size of the page it is rendering.
    total = len(matched)

    # Clamped rather than rejected: these arrive from a URL an admin may have
    # hand-edited, and an empty page or a slice taken from the wrong end of
    # the list is a worse answer than a sensible one.
    limit = max(1, min(MAX_HISTORY_LIMIT, limit))
    offset = max(0, offset)
    page = matched[offset:offset + limit]

    items = [{key: value for key, value in row.items() if not key.startswith("_")} for row in page]
    return {"items": items, "total": total, "limit": limit, "offset": offset}
