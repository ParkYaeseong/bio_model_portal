from __future__ import annotations

import base64 as _base64
from pathlib import Path

from sqlalchemy.orm import Session

from .. import chaining, chaining_exec, models
from ..runpod import PIPELINES
from ..chaining_exec import _ACTIVE_STATUSES, _owned_job
from ..workflow import job_bridge  # noqa: F401 -- tests patch tools.job_bridge.create_step_job
from . import files as mcp_files

# Artifact suffixes worth returning as text (the rest come back base64-encoded).
_TEXT_SUFFIXES = {
    ".json", ".csv", ".tsv", ".txt", ".log", ".md", ".yaml", ".yml",
    ".pdb", ".cif", ".mmcif", ".fasta", ".fa", ".faa", ".fna", ".sdf", ".a3m",
}
_DEFAULT_READ_BYTES = 200_000
# Above this, inlining/chunking a file costs more context than it is worth
# (base64 is 4/3 the bytes, and ~1 token per 3 chars); tell the caller so.
_INLINE_HINT_BYTES = 64 * 1024


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
    applied: dict = {}
    try:
        job = chaining_exec.submit_chained(
            db, user,
            pipeline=str(arguments.get("pipeline") or ""),
            params=arguments.get("parameters") or {},
            sequence=arguments.get("sequence"),
            files=arguments.get("files"),
            from_job_id=arguments.get("from_job_id"),
            source_artifact_ids=arguments.get("source_artifact_ids"),
            applied_defaults=applied,
        )
    except (ValueError, chaining.ChainError) as exc:
        return {"ok": False, "error": str(exc)}
    out = {"ok": True, "job_id": job.id, "status": job.status, "endpoint_id": job.endpoint_id}
    if applied:
        out["applied_defaults"] = applied
        out["note"] = ("these required parameters were not given, so the portal's "
                       "recommended defaults were used — tell the user")
    return out


def run_chain(db: Session, user: models.User, arguments: dict) -> dict:
    steps = arguments.get("steps") or []
    if not steps:
        return {"ok": False, "error": "steps is required (at least one step)"}
    for s in steps:
        p = str((s or {}).get("pipeline") or "")
        if p not in PIPELINES:
            return {"ok": False, "error": f"unknown pipeline '{p}'. valid: {sorted(PIPELINES)}"}
    first = steps[0]
    applied: dict = {}
    try:
        job = chaining_exec.submit_chained(
            db, user,
            pipeline=str(first.get("pipeline")),
            params=first.get("parameters") or {},
            sequence=arguments.get("sequence"),
            files=arguments.get("files"),
            from_job_id=first.get("from_job_id"),
            source_artifact_ids=first.get("source_artifact_ids"),
            applied_defaults=applied,
        )
    except (ValueError, chaining.ChainError) as exc:
        return {"ok": False, "error": str(exc)}
    rest = steps[1:]
    if rest:
        db.add(models.PendingChain(
            user_id=user.id, source_job_id=job.id, steps=rest, status="pending"))
        db.commit()
    out = {"ok": True, "first_job_id": job.id, "first_status": job.status,
           "queued": [str(s.get("pipeline")) for s in rest]}
    if applied:
        out["applied_defaults"] = applied
    return out


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
    return {"ok": True, "job_id": job.id, "status": job.status,
            "error_message": job.error_message, "artifacts": artifacts,
            "download_hint": "call download_artifact(job_id, artifact_id) to read an "
                             "artifact's contents (or GET /api/jobs/{job_id}/artifacts/{artifact_id})"}


def cancel_job(db: Session, user: models.User, arguments: dict) -> dict:
    job = _owned_job(db, user, arguments.get("job_id"))
    if not job:
        return {"ok": False, "error": "job not found"}
    if (job.status or "").lower() in _ACTIVE_STATUSES:
        job.status = "cancelled"
        db.commit()
    return {"ok": True, "job_id": job.id, "status": job.status}


def download_artifact(db: Session, user: models.User, arguments: dict) -> dict:
    """Read a finished job's artifact so the agent can act on the numbers
    instead of just naming the file."""
    job = _owned_job(db, user, arguments.get("job_id"))
    if not job:
        return {"ok": False, "error": "job not found"}
    artifact_id = str(arguments.get("artifact_id") or "").strip()
    file_name = str(arguments.get("file_name") or "").strip()
    if not artifact_id and not file_name:
        return {"ok": False, "error": "artifact_id or file_name is required "
                                      "(job_result lists both)"}
    matches = [
        a for a in job.artifacts
        if (artifact_id and a.id == artifact_id) or (not artifact_id and a.file_name == file_name)
    ]
    if not matches:
        return {"ok": False, "error": f"artifact not found in job {job.id}; available: "
                                      f"{[a.file_name for a in job.artifacts]}"}
    if len(matches) > 1:
        return {"ok": False, "error": f"file_name '{file_name}' is ambiguous; pass artifact_id. "
                                      f"Candidates: {[a.id for a in matches]}"}
    artifact = matches[0]
    path = Path(artifact.file_path)
    if not path.exists():
        return {"ok": False, "error": f"artifact file is gone from storage: {artifact.file_name}"}

    try:
        offset = max(0, int(arguments.get("offset") or 0))
        limit = int(arguments.get("max_bytes") or _DEFAULT_READ_BYTES)
    except (TypeError, ValueError):
        return {"ok": False, "error": "offset/max_bytes must be integers"}
    limit = max(1, min(limit, mcp_files.MAX_FILE_BYTES))

    total = path.stat().st_size
    with path.open("rb") as handle:
        handle.seek(offset)
        raw = handle.read(limit)

    encoding = str(arguments.get("encoding") or "auto").lower()
    if encoding == "auto":
        encoding = "text" if path.suffix.lower() in _TEXT_SUFFIXES else "base64"
    if encoding == "text":
        content = raw.decode("utf-8", errors="replace")
    else:
        encoding = "base64"
        content = _base64.b64encode(raw).decode("ascii")

    out = {
        "ok": True, "job_id": job.id, "artifact_id": artifact.id,
        "file_name": artifact.file_name, "kind": artifact.kind,
        "size_bytes": total, "offset": offset, "returned_bytes": len(raw),
        "truncated": offset + len(raw) < total,
        "encoding": encoding, "content": content,
    }
    if arguments.get("save_to_workspace"):
        # Staged so it can be fed straight back in as files=[{"path": file_id}]
        # without ever round-tripping the bytes through the model's context.
        out["workspace"] = mcp_files.save_workspace_file(
            user, artifact.file_name, path.read_bytes())
    return out


def upload_file(db: Session, user: models.User, arguments: dict) -> dict:
    """Stage an input file in the caller's server-side workspace, once, so runs
    can reference it by file_id instead of re-sending the bytes."""
    name = str(arguments.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "name is required (the file name to store, e.g. 'ab.pdb')"}
    sources = [k for k in ("base64", "text", "path") if str(arguments.get(k) or "").strip()]
    if not sources:
        return {"ok": False, "error": "provide the file content as base64=..., text=... "
                                      "(PDB/CIF/FASTA/SDF), or path=<file on this server>"}
    try:
        if "path" in sources:
            source = mcp_files.resolve_workspace_path(user, str(arguments["path"]), "path")
            data = source.read_bytes()
        elif "base64" in sources:
            data = mcp_files._decode_base64(arguments["base64"], "base64")
        else:
            data = str(arguments["text"]).encode("utf-8")
        stored = mcp_files.save_workspace_file(
            user, name, data, append=bool(arguments.get("append")))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    out = {"ok": True, **stored,
           "usage": f"pass files=[{{\"file_id\": \"{stored['file_id']}\"}}] to run_model."}
    if "path" not in sources and len(data) > _INLINE_HINT_BYTES:
        out["tip"] = (
            f"that was {len(data)} bytes sent inline (~{len(data) * 4 // 9000}k tokens as "
            f"base64). If you can run shell on this server, stop encoding: "
            f"`cp <file> {mcp_files.workspace_dir(user)}/` and pass "
            f'files=[{{"path": "<file name>"}}] instead. Chunking with append=true does '
            f"not reduce the total cost."
        )
    return out


def list_files(db: Session, user: models.User, arguments: dict) -> dict:
    """List the caller's staged workspace files (and where they live on disk)."""
    return {
        "ok": True,
        "workspace_dir": str(mcp_files.workspace_dir(user)),
        "files": mcp_files.list_workspace_files(user),
        "usage": "reference any of these as files=[{\"file_id\": \"<file_id>\"}]. A client "
                 "running on this server can also write straight into workspace_dir and "
                 "pass files=[{\"path\": \"<file name>\"}].",
    }


# One `files` entry. `name` is a FILE NAME, never a path the server reads — a
# name-only entry used to be dropped silently and the job failed later in the
# adapter, so the contract is now explicit here and enforced at submit time.
_FILE_ITEM = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "file name, e.g. 'ab.pdb' (NOT a path)"},
        "base64": {"type": "string", "description": "file bytes, base64-encoded"},
        "text": {"type": "string", "description": "file text for text formats (PDB/CIF/FASTA/SDF)"},
        "path": {"type": "string", "description": "path of a file in your server-side workspace "
                                                  "(see the list_files tool)"},
        "file_id": {"type": "string", "description": "id returned by the upload_file tool"},
    },
    "anyOf": [
        {"required": ["base64"]}, {"required": ["text"]},
        {"required": ["path"]}, {"required": ["file_id"]},
    ],
}
_FILES_DESC = (
    "Input files. EVERY entry must carry real content — a 'name' alone is "
    "rejected, because the server cannot read a local path from it. Pick the "
    "cheapest form that applies, in this order: (1) if you can run shell "
    "commands on the portal's own host, copy the file into the workspace "
    "directory that list_files reports and pass {'path': '<file name>'} — this "
    "costs no tokens and no encoding; (2) for a SMALL text file (PDB/CIF/FASTA/"
    "SDF under ~50 KB) pass {'text': '<file text>'}; (3) otherwise upload it "
    "once with upload_file and pass {'file_id': ...}. Never base64 a large "
    "structure into this call: a 200 KB PDB is ~75k tokens of base64, and "
    "splitting it into chunks costs exactly the same."
)

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
            "files": {"type": "array", "description": _FILES_DESC, "items": _FILE_ITEM},
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
            "files": {"type": "array", "description": _FILES_DESC + " These feed STEP 1.",
                      "items": _FILE_ITEM},
        },
        "required": ["steps"],
    }),
    "job_status": (job_status, "Get a job's status.", {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]}),
    "job_result": (job_result, "Get a job's artifacts + metrics for explaining results.", {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]}),
    "cancel_job": (cancel_job, "Cancel a running job.", {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]}),
    "download_artifact": (download_artifact,
        "Read the CONTENT of one artifact from a finished job (job_result lists them). "
        "Text artifacts (result JSON/CSV, PDB/CIF, FASTA) come back as text, binary as "
        "base64; use offset/max_bytes to page through a big file. Use this to actually "
        "read scores/sequences — e.g. to pull the designed sequences or ddG table out of "
        "a run — instead of only reporting file names. Pass save_to_workspace=true to "
        "also stage it as an input file for a follow-up run.", {
        "type": "object",
        "properties": {
            "job_id": {"type": "string"},
            "artifact_id": {"type": "string"},
            "file_name": {"type": "string", "description": "alternative to artifact_id"},
            "offset": {"type": "integer", "description": "byte offset to start reading (default 0)"},
            "max_bytes": {"type": "integer", "description": f"max bytes to return (default {_DEFAULT_READ_BYTES})"},
            "encoding": {"type": "string", "enum": ["auto", "text", "base64"]},
            "save_to_workspace": {"type": "boolean"},
        },
        "required": ["job_id"],
    }),
    "upload_file": (upload_file,
        "Stage an input file on the server ONCE and get a file_id back, then run models "
        "with files=[{\"file_id\": \"...\"}]. CHECK FIRST whether you can run shell on the "
        "portal's own host: if so, do NOT use this tool — `cp` the file into the directory "
        "list_files reports and pass files=[{\"path\": \"<file name>\"}], which costs "
        "nothing. Use this tool when your client is remote: text=<file text> for a text "
        "format, base64=<bytes> otherwise, or path=<file already inside your workspace>. "
        "Chunking (same name + append=true) makes a big upload survivable, NOT cheaper — "
        "the token cost is the same total, so only base64 a file you genuinely cannot "
        "reach any other way. The returned size_bytes/sha256 verify it arrived intact.", {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "file name to store, e.g. 'ab.pdb'"},
            "text": {"type": "string"},
            "base64": {"type": "string"},
            "path": {"type": "string", "description": "path of an existing file on the server"},
            "append": {"type": "boolean", "description": "append this chunk to an existing file"},
        },
        "required": ["name"],
    }),
    "list_files": (list_files,
        "List the files you have staged on the server (file_id, size, sha256) and report "
        "your absolute workspace directory. CALL THIS FIRST when you need to feed a local "
        "file to a model: if you can run shell on the portal's host, `cp` the file into "
        "workspace_dir and pass files=[{\"path\": \"<name>\"}] instead of encoding it.",
        {"type": "object", "properties": {}}),
}
