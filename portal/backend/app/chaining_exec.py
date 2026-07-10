"""Shared resolve-and-submit for chained model runs.

Used by the chat tools (run_model / run_chain) and the JobMonitor (dependent
pending-chain steps). Owns the file-staging temp dir and the chaining
resolution; raises ValueError (input problems) or chaining.ChainError.
"""
from __future__ import annotations

import base64
import re
import shutil
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from . import chaining, models
from .runpod import PIPELINES
from .workflow import job_bridge

_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB per file
_ACTIVE_STATUSES = {"pending", "submitted", "running", "queued", "in_queue", "in_progress", "processing"}


def _owned_job(db: Session, user: models.User, job_id: str) -> models.Job | None:
    return db.query(models.Job).filter_by(id=str(job_id or ""), user_id=user.id).first()


def submit_chained(
    db: Session,
    user: models.User,
    *,
    pipeline: str,
    params: dict,
    sequence: str | None = None,
    files: list[dict] | None = None,
    from_job_id: str | None = None,
    source_artifact_ids: list[str] | None = None,
) -> models.Job:
    """Resolve any chained inputs and submit one job. Raises ValueError or
    chaining.ChainError; callers format those for the LLM / mark the chain."""
    pipeline = str(pipeline or "")
    if pipeline not in PIPELINES:
        raise ValueError(f"unknown pipeline '{pipeline}'. valid: {sorted(PIPELINES)}")
    params = params or {}
    input_files: list[Path] = []
    tmp: Path | None = None
    try:
        for item in files or []:
            b64 = item.get("base64")
            if not b64:
                continue
            try:
                data = base64.b64decode(b64)
            except Exception as exc:
                raise ValueError("a file's base64 content is invalid") from exc
            if len(data) > _MAX_UPLOAD_BYTES:
                raise ValueError(f"file exceeds {_MAX_UPLOAD_BYTES} bytes")
            tmp = tmp or Path(tempfile.mkdtemp(prefix="mcp_upload_"))
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(item.get("name") or "input"))
            dest = tmp / (safe or "input")
            dest.write_bytes(data)
            input_files.append(dest)

        if from_job_id or source_artifact_ids:
            if not from_job_id:
                raise ValueError("from_job_id is required when source_artifact_ids is set")
            src = _owned_job(db, user, from_job_id)
            if not src:
                raise ValueError("source job not found")
            if (src.status or "").lower() in _ACTIVE_STATUSES:
                raise ValueError(f"source job {src.id} is not finished (status={src.status})")
            plan = chaining.plan_chain(db, src, pipeline, source_artifact_ids)
            if plan.delivery == "sequence":
                if not (sequence and str(sequence).strip()):
                    sequence = plan.sequence
            else:
                tmp = tmp or Path(tempfile.mkdtemp(prefix="mcp_chain_"))
                for artifact in plan.artifacts:
                    safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(artifact.file_name).name) or "input"
                    dest = tmp / safe
                    dest.write_bytes(Path(artifact.file_path).read_bytes())
                    input_files.append(dest)

        if pipeline == "rfdiffusion" and not (
            input_files or params.get("length") or params.get("contigs") or params.get("contig")
        ):
            raise ValueError(
                "RFdiffusion needs a design spec: set 'length' for de novo (recommended: 100), "
                "or provide a PDB file with 'contigs' for motif scaffolding. A sequence is not a "
                "valid RFdiffusion input. Ask the user for a length, or offer the recommended "
                "default (length=100) and confirm before running."
            )

        return job_bridge.create_step_job(
            db, user_id=user.id, title=f"mcp {pipeline}", pipeline=pipeline,
            params=params, input_files=input_files or None, sequence=sequence,
        )
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
