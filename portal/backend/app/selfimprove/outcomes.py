from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from .. import models

_METRIC_KEYS = ("plddt", "mean_plddt", "ptm", "iptm", "pae")
_SUCCESS = "completed"


def job_metrics(job: models.Job) -> dict:
    """Best-effort scalar metrics parsed from the job's output.json, if any."""
    metrics: dict = {}
    base = Path(job.result_dir or "")
    if not base.exists():
        return metrics
    for path in base.rglob("output.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, dict):
            for k in _METRIC_KEYS:
                v = data.get(k)
                if isinstance(v, (int, float)):
                    metrics.setdefault(k, float(v))
        break
    return metrics


def job_outcome(db: Session, job_id: str) -> dict:
    """Live read of a job's terminal outcome. success == status 'completed'."""
    job = db.query(models.Job).filter_by(id=str(job_id or "")).first()
    if not job:
        return {"status": None, "success": False, "metrics": {}}
    return {
        "status": job.status,
        "success": (job.status or "").lower() == _SUCCESS,
        "metrics": job_metrics(job),
    }
