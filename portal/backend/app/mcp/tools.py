from __future__ import annotations

import base64
import re
import shutil
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from .. import models
from ..runpod import PIPELINES
from ..workflow import job_bridge
from .. import chaining

_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB per file
_ACTIVE_STATUSES = {"pending", "submitted", "running", "queued", "in_queue", "in_progress", "processing"}


def _field(f) -> dict:
    return {
        "name": f.name, "label": f.label, "field_type": f.field_type,
        "required": bool(f.required), "options": f.options,
        "placeholder": f.placeholder, "helper": f.helper, "minimum": f.minimum,
    }


def list_models(db: Session, user: models.User, arguments: dict) -> dict:
    models_out = [
        {
            "key": p.key, "label": p.label, "description": p.description,
            "supports_sequence": p.supports_sequence, "requires_archive": p.requires_archive,
            "input_fields": [_field(f) for f in p.input_fields],
        }
        for p in PIPELINES.values()
    ]
    return {"ok": True, "models": models_out}


def run_model(db: Session, user: models.User, arguments: dict) -> dict:
    pipeline = str(arguments.get("pipeline") or "")
    if pipeline not in PIPELINES:
        return {"ok": False, "error": f"unknown pipeline '{pipeline}'. valid: {sorted(PIPELINES)}"}
    params = arguments.get("parameters") or {}
    sequence = arguments.get("sequence")
    from_job_id = arguments.get("from_job_id")
    source_artifact_ids = arguments.get("source_artifact_ids")

    input_files: list[Path] = []
    tmp: Path | None = None
    try:
        for item in arguments.get("files") or []:
            b64 = item.get("base64")
            if not b64:
                continue
            try:
                data = base64.b64decode(b64)
            except Exception:
                return {"ok": False, "error": "a file's base64 content is invalid"}
            if len(data) > _MAX_UPLOAD_BYTES:
                return {"ok": False, "error": f"file exceeds {_MAX_UPLOAD_BYTES} bytes"}
            tmp = tmp or Path(tempfile.mkdtemp(prefix="mcp_upload_"))
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(item.get("name") or "input"))
            dest = tmp / (safe or "input")
            dest.write_bytes(data)
            input_files.append(dest)

        if from_job_id or source_artifact_ids:
            if not from_job_id:
                return {"ok": False, "error": "from_job_id is required when source_artifact_ids is set"}
            src = _owned_job(db, user, from_job_id)
            if not src:
                return {"ok": False, "error": "source job not found"}
            if (src.status or "").lower() in _ACTIVE_STATUSES:
                return {"ok": False, "error": f"source job {src.id} is not finished (status={src.status})"}
            try:
                plan = chaining.plan_chain(db, src, pipeline, source_artifact_ids)
            except chaining.ChainError as exc:
                return {"ok": False, "error": str(exc)}
            if plan.delivery == "sequence":
                if not (sequence and str(sequence).strip()):
                    sequence = plan.sequence
            else:
                tmp = tmp or Path(tempfile.mkdtemp(prefix="mcp_chain_"))
                for artifact in plan.artifacts:
                    dest = tmp / artifact.file_name
                    dest.write_bytes(Path(artifact.file_path).read_bytes())
                    input_files.append(dest)

        job = job_bridge.create_step_job(
            db, user_id=user.id, title=f"mcp {pipeline}", pipeline=pipeline,
            params=params, input_files=input_files or None, sequence=sequence,
        )
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)  # bytes already copied into uploads_dir
    return {"ok": True, "job_id": job.id, "status": job.status, "endpoint_id": job.endpoint_id}


def _owned_job(db: Session, user: models.User, job_id: str) -> models.Job | None:
    return db.query(models.Job).filter_by(id=str(job_id or ""), user_id=user.id).first()


def job_status(db: Session, user: models.User, arguments: dict) -> dict:
    job = _owned_job(db, user, arguments.get("job_id"))
    if not job:
        return {"ok": False, "error": "job not found"}
    return {"ok": True, "job_id": job.id, "pipeline": job.pipeline,
            "status": job.status, "error_message": job.error_message}


def job_result(db: Session, user: models.User, arguments: dict) -> dict:
    job = _owned_job(db, user, arguments.get("job_id"))
    if not job:
        return {"ok": False, "error": "job not found"}
    artifacts = [
        {"id": a.id, "file_name": a.file_name, "kind": a.kind, "size_bytes": a.size_bytes}
        for a in job.artifacts
    ]
    return {"ok": True, "job_id": job.id, "status": job.status, "artifacts": artifacts,
            "download_hint": "GET /api/jobs/{job_id}/artifacts/{artifact_id}"}


def cancel_job(db: Session, user: models.User, arguments: dict) -> dict:
    job = _owned_job(db, user, arguments.get("job_id"))
    if not job:
        return {"ok": False, "error": "job not found"}
    if (job.status or "").lower() in _ACTIVE_STATUSES:
        job.status = "cancelled"
        db.commit()
    return {"ok": True, "job_id": job.id, "status": job.status}


# name -> (callable, description, json input schema)
TOOLS = {
    "list_models": (list_models, "List the portal's models and each model's input fields/params.", {"type": "object", "properties": {}}),
    "run_model": (run_model,
        "Run a portal model. Use list_models first for valid pipeline keys and params. "
        "To use a previous job's output as this run's input (chaining, e.g. dock the backbone "
        "an RFdiffusion job produced), pass from_job_id=<that job's id>; the server injects its "
        "compatible outputs (a structure PDB is fed as an input file; a designed sequence is fed "
        "as the sequence). Optionally pass source_artifact_ids to pick specific artifacts. For "
        "DiffDock the ligand must still be provided via files or parameters.", {
        "type": "object",
        "properties": {
            "pipeline": {"type": "string"},
            "parameters": {"type": "object"},
            "sequence": {"type": "string"},
            "from_job_id": {"type": "string"},
            "source_artifact_ids": {"type": "array", "items": {"type": "string"}},
            "files": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "base64": {"type": "string"}}}},
        },
        "required": ["pipeline"],
    }),
    "job_status": (job_status, "Get a job's status.", {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]}),
    "job_result": (job_result, "Get a job's artifacts + metrics for explaining results.", {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]}),
    "cancel_job": (cancel_job, "Cancel a running job.", {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]}),
}
