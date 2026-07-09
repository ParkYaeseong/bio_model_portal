"""Queue-position + rough ETA estimates for jobs.

Workers report only coarse status (queued / in-progress / completed), never a
percentage, so the "progress" a user sees is derived here:

* queue position  = how many OTHER active jobs sit ahead of theirs on the same
  model endpoint (counted across all users — only the number is exposed).
* avg duration    = mean wall time (submit -> complete) of recent completed jobs
  of the same pipeline.
* ETA             = a deliberately-rough estimate built from the two above.

All numbers are approximate and labelled as such in the UI.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import models

# Mirrors tasks.ACTIVE_JOB_STATUSES. A job is "queued" until a worker picks it
# up, then "running". Kept local so this module has no heavy import chain.
RUNNING_STATUSES = {"running", "in_progress", "processing"}
QUEUED_STATUSES = {"pending", "submitted", "queued", "in_queue"}
ACTIVE_STATUSES = RUNNING_STATUSES | QUEUED_STATUSES


def queue_ahead(db: Session, job: models.Job) -> int:
    """Active jobs (any user) on the same endpoint submitted before this one."""
    if not job.endpoint_id or (job.status or "").lower() not in ACTIVE_STATUSES:
        return 0
    return (
        db.query(func.count(models.Job.id))
        .filter(
            models.Job.endpoint_id == job.endpoint_id,
            func.lower(models.Job.status).in_(ACTIVE_STATUSES),
            models.Job.created_at < job.created_at,
        )
        .scalar()
        or 0
    )


def average_durations(
    db: Session, *, min_samples: int = 3, scan: int = 200
) -> dict[str, float]:
    """Per-pipeline mean wall time (seconds) over recent completed jobs.

    Pipelines with fewer than ``min_samples`` completed jobs are omitted, so the
    caller shows "estimate unavailable" rather than a number built from noise.
    """
    rows = (
        db.query(models.Job.pipeline, models.Job.created_at, models.Job.updated_at)
        .filter(models.Job.status == "completed")
        .order_by(models.Job.updated_at.desc())
        .limit(scan)
        .all()
    )
    buckets: dict[str, list[float]] = {}
    for pipeline, created, updated in rows:
        if pipeline and created and updated and updated > created:
            buckets.setdefault(pipeline, []).append((updated - created).total_seconds())
    return {p: sum(v) / len(v) for p, v in buckets.items() if len(v) >= min_samples}


def build_estimate(
    *,
    status: str,
    elapsed_seconds: float,
    ahead: int,
    avg_seconds: Optional[float],
) -> Optional[dict]:
    """Pure math: turn (status, elapsed, queue position, avg) into UI fields.

    Returns ``None`` for terminal jobs (no estimate). ``eta_seconds`` /
    ``avg_seconds`` are omitted when there isn't enough history to estimate.
    """
    s = (status or "").lower()
    if s not in ACTIVE_STATUSES:
        return None
    out: dict = {
        "queue_position": max(0, int(ahead)),
        "elapsed_seconds": round(max(0.0, elapsed_seconds)),
    }
    if avg_seconds and avg_seconds > 0:
        out["avg_seconds"] = round(avg_seconds)
        if s in RUNNING_STATUSES:
            # Already running: time left ~ typical total minus time already spent.
            out["eta_seconds"] = round(max(0.0, avg_seconds - elapsed_seconds))
        else:
            # Still queued: the jobs ahead run first (single-worker assumption —
            # conservative), then ours. Rough by design.
            out["eta_seconds"] = round((max(0, int(ahead)) + 1) * avg_seconds)
    return out


def estimate_for_job(db: Session, job: models.Job, avgs: dict[str, float], now: datetime) -> Optional[dict]:
    """Compose the DB lookups + math for one job (convenience for the router)."""
    if (job.status or "").lower() not in ACTIVE_STATUSES:
        return None
    elapsed = (now - job.created_at).total_seconds() if job.created_at else 0.0
    return build_estimate(
        status=job.status,
        elapsed_seconds=elapsed,
        ahead=queue_ahead(db, job),
        avg_seconds=avgs.get(job.pipeline),
    )
