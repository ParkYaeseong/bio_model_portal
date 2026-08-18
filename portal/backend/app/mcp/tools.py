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
            "category": p.category, "tags": p.tags, "instructions": p.instructions,
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


def run_chain(db: Session, user: models.User, arguments: dict) -> dict:
    steps = arguments.get("steps") or []
    if not steps:
        return {"ok": False, "error": "steps is required (at least one step)"}
    for s in steps:
        p = str((s or {}).get("pipeline") or "")
        if p not in PIPELINES:
            return {"ok": False, "error": f"unknown pipeline '{p}'. valid: {sorted(PIPELINES)}"}
    first = steps[0]
    try:
        job = chaining_exec.submit_chained(
            db, user,
            pipeline=str(first.get("pipeline")),
            params=first.get("parameters") or {},
            sequence=arguments.get("sequence"),
            files=arguments.get("files"),
            from_job_id=first.get("from_job_id"),
            source_artifact_ids=first.get("source_artifact_ids"),
        )
    except (ValueError, chaining.ChainError) as exc:
        return {"ok": False, "error": str(exc)}
    rest = steps[1:]
    if rest:
        db.add(models.PendingChain(
            user_id=user.id, source_job_id=job.id, steps=rest, status="pending"))
        db.commit()
    return {"ok": True, "first_job_id": job.id, "first_status": job.status,
            "queued": [str(s.get("pipeline")) for s in rest]}


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
        "Before running, make sure required parameters are set — call list_models to see each "
        "model's fields (a field's 'placeholder' is the recommended default). If the user didn't "
        "give a required parameter, ASK them; if they still don't provide one, offer the "
        "recommended default and confirm before running. rfdiffusion needs 'length' (de novo, "
        "recommended 100) or a PDB + 'contigs' (motif) — a sequence is NOT valid rfdiffusion "
        "input. "
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
    "run_chain": (run_chain,
        "Before running, make sure required parameters are set — call list_models to see each "
        "model's fields (a field's 'placeholder' is the recommended default). If the user didn't "
        "give a required parameter, ASK them; if they still don't provide one, offer the "
        "recommended default and confirm before running. rfdiffusion needs 'length' (de novo, "
        "recommended 100) or a PDB + 'contigs' (motif) — a sequence is NOT valid rfdiffusion "
        "input. "
        "Run an ordered multi-step chain in one call (e.g. rfdiffusion then proteinmpnn "
        "then colabfold then diffdock). Step 1 runs immediately from the user's attached "
        "input/sequence; each later step runs AUTOMATICALLY when its predecessor finishes, "
        "feeding the predecessor's output in. Use this when the user asks to do several "
        "models in sequence in one message. A queued (non-first) step that needs an extra "
        "uploaded file (e.g. a DiffDock SDF ligand) is not supported — use a SMILES ligand "
        "in that step's parameters, or run it separately. After calling, tell the user step 1 "
        "started and which steps are queued.", {
        "type": "object",
        "properties": {
            "steps": {"type": "array", "items": {"type": "object", "properties": {
                "pipeline": {"type": "string"},
                "parameters": {"type": "object"},
            }, "required": ["pipeline"]}},
            "sequence": {"type": "string"},
        },
        "required": ["steps"],
    }),
    "job_status": (job_status, "Get a job's status.", {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]}),
    "job_result": (job_result, "Get a job's artifacts + metrics for explaining results.", {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]}),
    "cancel_job": (cancel_job, "Cancel a running job.", {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]}),
}
