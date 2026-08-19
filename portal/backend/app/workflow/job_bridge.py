from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from .. import models
from ..runpod import RunpodClient, build_pipeline_payload, pipeline_endpoint
from ..storage import build_archive, file_to_base64, uploads_dir


def create_step_job(
    db: Session,
    *,
    user_id: int,
    title: str,
    pipeline: str,
    params: dict,
    input_files: list[Path] | None = None,
    sequence: str | None = None,
) -> models.Job:
    """Create + submit a portal Job for a workflow step, reusing the gateway
    path so the existing JobMonitor drives it to completion."""
    params = {k: v for k, v in (params or {}).items() if not (isinstance(v, str) and v.strip() == "")}
    job = models.Job(
        id=str(uuid4()), user_id=user_id, title=title, pipeline=pipeline,
        status="pending", parameters=params,
        expires_at=datetime.utcnow() + timedelta(days=365),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    input_archive = None
    if input_files:
        root = uploads_dir(user_id, job.id)
        saved: list[Path] = []
        for src in input_files:
            dest = root / src.name
            dest.write_bytes(Path(src).read_bytes())
            saved.append(dest)
        archive_path = root / "inputs.tar.gz"
        build_archive(saved, archive_path, base_dir=root)
        input_archive = {
            "file_names": [p.name for p in saved],
            "kind": "uploaded",
            "archive_name": "inputs.tar.gz",
            "base64": file_to_base64(archive_path),
        }
        job.input_archive_path = str(archive_path)

    endpoint_id = pipeline_endpoint(pipeline)
    job.endpoint_id = endpoint_id
    payload = build_pipeline_payload(pipeline, parameters=params, sequence=sequence, input_archive=input_archive)
    runpod_job_id = RunpodClient().submit(endpoint_id, payload)
    job.runpod_job_id = runpod_job_id
    job.status = "submitted"
    db.commit()
    db.refresh(job)
    return job
