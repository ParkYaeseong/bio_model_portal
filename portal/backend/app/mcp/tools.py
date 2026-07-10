from __future__ import annotations

from sqlalchemy.orm import Session

from .. import chaining, chaining_exec, models
from ..runpod import PIPELINES
from ..chaining_exec import _ACTIVE_STATUSES, _owned_job
from ..workflow import job_bridge  # noqa: F401 -- tests patch tools.job_bridge.create_step_job


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
    try:
        job = chaining_exec.submit_chained(
            db, user,
            pipeline=str(arguments.get("pipeline") or ""),
            params=arguments.get("parameters") or {},
            sequence=arguments.get("sequence"),
            files=arguments.get("files"),
            from_job_id=arguments.get("from_job_id"),
            source_artifact_ids=arguments.get("source_artifact_ids"),
        )
    except (ValueError, chaining.ChainError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "job_id": job.id, "status": job.status, "endpoint_id": job.endpoint_id}


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
