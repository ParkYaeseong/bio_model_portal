"""Stopping a job's actual compute, not just its row.

Shared by the REST cancel endpoint and the MCP/chatbot cancel tool. It lived in
routers/jobs.py, where the MCP path could not reach it — so `cancel_job` over
MCP only flipped the database status and left the GPU running, which is exactly
backwards from what a user cancelling an AlphaFold3 run wants.
"""
from __future__ import annotations

from . import models, queue_estimate
from .runpod import RunpodClient


def cancel_active_job(job: models.Job) -> bool:
    """Best-effort stop of a still-running job's compute (RunPod/worker via the
    gateway) and mark it cancelled locally. Returns True if it was active. Never
    raises — the local state is updated even if the remote call fails, so the
    job leaves the active set either way."""
    if (job.status or "").lower() not in queue_estimate.ACTIVE_STATUSES:
        return False
    if job.endpoint_id and job.runpod_job_id:
        try:
            RunpodClient().cancel(job.endpoint_id, job.runpod_job_id)
        except Exception:  # noqa: BLE001 — best-effort; still cancel locally
            pass
    job.status = "cancelled"
    job.error_message = "사용자가 정지함"
    return True
