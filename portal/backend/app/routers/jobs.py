from __future__ import annotations

import base64
import copy
import csv
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any, List

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
import httpx
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .. import models, queue_estimate
from ..auth import get_current_user
from ..database import get_db
from ..runpod import PIPELINES, RunpodClient, build_pipeline_payload, pipeline_endpoint
from ..schemas import JobRead
from ..storage import build_archive, file_to_base64, remove_tree, save_uploads, uploads_dir

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

DIFFDOCK_DATA_DIR = "data"
DIFFDOCK_INPUTS_DIR = "inputs"
DIFFDOCK_OUT_DIR = "results/"
DIFFDOCK_CONFIG = "default_inference_args.yaml"
FASTA_SUFFIXES = (".fasta", ".fa", ".fna", ".ffn", ".faa")

@router.get("", response_model=List[JobRead])
def list_jobs(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    is_admin = getattr(current_user, "is_admin", False)
    query = db.query(models.Job)
    if not is_admin:
        query = query.filter(models.Job.user_id == current_user.id)
    jobs = query.order_by(models.Job.created_at.desc()).all()
    # Attach a rough queue-position + ETA to any still-active job. Queue position
    # counts other users' jobs ahead on the same endpoint (number only).
    avgs = queue_estimate.average_durations(db)
    now = datetime.utcnow()
    out: list[JobRead] = []
    for job in jobs:
        read = JobRead.model_validate(job, from_attributes=True)
        est = queue_estimate.estimate_for_job(db, job, avgs, now)
        if est:
            read = read.model_copy(update=est)
        if is_admin and job.user_id != current_user.id:
            read = read.model_copy(update={"owner": job.user.username})
        out.append(read)
    return out


@router.get("/{job_id}", response_model=JobRead)
def get_job(job_id: str, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    job = _get_job_or_404(db, current_user, job_id)
    return job


@router.post("", response_model=JobRead)
async def create_job(
    title: str = Form(...),
    pipeline: str = Form(...),
    parameters: str = Form("{}"),
    preferred_download_dir: str | None = Form(None),
    sequence: str | None = Form(None),
    files: List[UploadFile] = File(default_factory=list),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if pipeline not in PIPELINES:
        raise HTTPException(status_code=400, detail="Unknown pipeline.")
    try:
        parameter_data = json.loads(parameters or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid parameter payload.") from exc
    sequence_text = (sequence or "").strip()
    payload_parameters = copy.deepcopy(parameter_data)

    if pipeline == "diffdock":
        jobs_payload = parameter_data.get("jobs")
        if not isinstance(jobs_payload, list) or not jobs_payload:
            raise HTTPException(status_code=400, detail="DiffDock jobs definition is required.")
        for idx, item in enumerate(jobs_payload, start=1):
            if not isinstance(item, dict):
                raise HTTPException(status_code=400, detail=f"Invalid job entry at index {idx}.")
            required_keys = ("complex_name", "protein_path", "ligand_description")
            if not all(item.get(key) for key in required_keys):
                raise HTTPException(status_code=400, detail=f"Missing required DiffDock fields in job #{idx}.")
            ligand_type = item.get("ligand_type")
            if ligand_type not in {"sdf", "smiles"}:
                raise HTTPException(status_code=400, detail=f"Unsupported ligand type in job #{idx}.")

    phastest_input_type = None
    if pipeline == "phastest":
        phastest_input_type = parameter_data.get("input_type")
        mode = parameter_data.get("mode")
        sample_name = parameter_data.get("sample_name")
        if phastest_input_type not in {"fasta", "contig", "genbank"}:
            raise HTTPException(status_code=400, detail="Invalid PHASTEST input type.")
        if mode not in {"lite", "deep"}:
            raise HTTPException(status_code=400, detail="Invalid PHASTEST mode.")
        if not sample_name:
            raise HTTPException(status_code=400, detail="Sample name is required for PHASTEST.")
        if phastest_input_type == "genbank" and not parameter_data.get("accession"):
            raise HTTPException(status_code=400, detail="GenBank accession is required for this mode.")

    job = models.Job(
        title=title,
        pipeline=pipeline,
        preferred_download_dir=preferred_download_dir,
        parameters=parameter_data,
        user_id=current_user.id,
        status="pending",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    archive_payload = None
    saved_files: list[Path] = []
    uploads_root: Path | None = None
    file_list: list[UploadFile] = list(files or [])
    if file_list:
        saved_files, uploads_root = save_uploads(current_user.id, job.id, file_list)

    sequence_payload = sequence_text or None
    if pipeline == "alphafold" and sequence_payload and parameter_data.get("model_preset") == "multimer":
        fasta_entries = _parse_fasta_entries(sequence_payload)
        if len(fasta_entries) < 2:
            raise HTTPException(status_code=400, detail="AlphaFold Multimer 모드에서는 FASTA 헤더(>chainA 등)를 사용해 체인별 서열을 2개 이상 입력하세요.")
        uploads_root = uploads_root or uploads_dir(current_user.id, job.id)
        generated_path = uploads_root / "sequence_multimer_input.fasta"
        _write_fasta_file(generated_path, fasta_entries)
        saved_files.append(generated_path)
        sequence_payload = None

    relative_names: list[str] = []
    relative_map: dict[str, Path] = {}
    if saved_files:
        uploads_root = uploads_root or uploads_dir(current_user.id, job.id)
        archive_path = uploads_root / "inputs.tar.gz"
        build_archive(saved_files, archive_path, base_dir=uploads_root)
        for path in saved_files:
            rel_name = _relative_name(path, uploads_root)
            relative_names.append(rel_name)
            relative_map[rel_name] = path
            # Allow clients to reference either "foo/bar" or "inputs/foo/bar"
            prefixed = rel_name if rel_name.startswith("inputs/") else f"inputs/{rel_name}"
            relative_map[prefixed] = path
        archive_payload = {
            **_build_archive_meta(pipeline, relative_names),
            "archive_name": "inputs.tar.gz",
            "base64": file_to_base64(archive_path),
        }
        job.input_archive_path = str(archive_path)

    if pipeline == "phastest" and phastest_input_type in {"fasta", "contig"}:
        fasta_candidates = [path for path in saved_files if path.suffix.lower() in FASTA_SUFFIXES]
        if len(fasta_candidates) != 1:
            raise HTTPException(status_code=400, detail="PHASTEST FASTA/contig 모드에서는 FASTA 파일 1개만 업로드하세요.")
        payload_parameters["fasta_file"] = file_to_base64(fasta_candidates[0])
        archive_payload = None

    if pipeline == "diffdock":
        payload_parameters = _build_diffdock_payload(parameter_data.get("jobs"), relative_map)
        archive_payload = None

    pipeline_meta = PIPELINES[pipeline]
    requires_archive = pipeline_meta.requires_archive
    if pipeline == "phastest":
        if phastest_input_type == "genbank":
            requires_archive = False
        elif phastest_input_type in {"fasta", "contig"}:
            requires_archive = False

    if requires_archive and not saved_files:
        raise HTTPException(status_code=400, detail="This pipeline requires file uploads.")

    endpoint_id = pipeline_endpoint(pipeline)
    job.endpoint_id = endpoint_id

    payload = build_pipeline_payload(
        pipeline,
        parameters=payload_parameters,
        sequence=sequence_payload if pipeline_meta.supports_sequence else None,
        input_archive=archive_payload,
    )

    try:
        client = RunpodClient()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    try:
        runpod_job_id = client.submit(endpoint_id, payload)
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:1000]
        raise HTTPException(status_code=exc.response.status_code, detail=f"RunPod submit failed: {detail}") from exc
    job.runpod_job_id = runpod_job_id
    job.status = "submitted"
    db.add(job)
    db.commit()
    db.refresh(job)

    return job


@router.get("/{job_id}/download")
def download_archive(job_id: str, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    job = _get_job_or_404(db, current_user, job_id)
    if not job.result_archive:
        raise HTTPException(status_code=404, detail="Results are not ready yet.")
    return FileResponse(job.result_archive, filename=Path(job.result_archive).name)


def _cancel_active_job(job: models.Job) -> bool:
    """Best-effort stop of a still-running job's actual compute (RunPod/worker
    via the gateway) and mark it cancelled locally. Returns True if it was
    active. Never raises — the local state is updated even if the remote call
    fails, so the job leaves the active set either way."""
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


@router.post("/{job_id}/cancel", response_model=JobRead)
def cancel_job(job_id: str, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    job = _get_job_or_404(db, current_user, job_id)
    _cancel_active_job(job)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.delete("/{job_id}")
def delete_job(job_id: str, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    job = _get_job_or_404(db, current_user, job_id)
    # Deleting an active job must stop its compute first, or it keeps running on
    # the GPU untracked.
    _cancel_active_job(job)
    if job.result_dir:
        remove_tree(Path(job.result_dir))
    if job.input_archive_path:
        archive_path = Path(job.input_archive_path)
        if archive_path.exists():
            remove_tree(archive_path)
        uploads_dir = archive_path.parent
        if uploads_dir.exists():
            remove_tree(uploads_dir)
    db.delete(job)
    db.commit()
    return {"ok": True}


@router.get("/{job_id}/artifacts/{artifact_id}")
def download_artifact(
    job_id: str,
    artifact_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _get_job_or_404(db, current_user, job_id)
    artifact = db.query(models.Artifact).filter(models.Artifact.id == artifact_id, models.Artifact.job_id == job_id).first()
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found.")
    return FileResponse(artifact.file_path, filename=artifact.file_name, media_type=artifact.mime_type or "application/octet-stream")


def _get_job_or_404(db: Session, current_user: models.User, job_id: str) -> models.Job:
    query = db.query(models.Job).filter(models.Job.id == job_id)
    if not getattr(current_user, "is_admin", False):
        query = query.filter(models.Job.user_id == current_user.id)
    job = query.first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


def _build_archive_meta(pipeline: str, relative_names: list[str]) -> dict:
    meta: dict[str, Any] = {"file_names": [Path(name).name for name in relative_names]}
    if pipeline == "alphafold":
        root_candidates = {_top_level_dir(name) for name in relative_names if "/" in name}
        root_candidates.discard("")
        if root_candidates and len(root_candidates) == 1:
            meta["kind"] = "fasta_dir"
            meta["root"] = root_candidates.pop()
        else:
            meta["kind"] = "fasta_paths"
    else:
        meta["kind"] = "uploaded"
    return meta


def _top_level_dir(name: str) -> str:
    return name.split("/", 1)[0] if "/" in name else ""


def _relative_name(path: Path, root: Path | None) -> str:
    candidate = path.name
    if root is not None:
        try:
            candidate = path.relative_to(root).as_posix()
        except ValueError:
            candidate = path.name
    return candidate.replace("\\", "/")


def _build_diffdock_payload(jobs_payload: Any, relative_map: dict[str, Path]) -> dict[str, Any]:
    if not isinstance(jobs_payload, list) or not jobs_payload:
        raise HTTPException(status_code=400, detail="DiffDock 복합체 정보가 비어 있습니다.")

    csv_buffer = io.StringIO()
    writer = csv.writer(csv_buffer)
    writer.writerow(["complex_name", "protein_path", "ligand_description", "protein_sequence"])

    referenced_paths: set[str] = set()
    for idx, item in enumerate(jobs_payload, start=1):
        raw_name = item.get("complex_name")
        if raw_name is None:
            complex_name = ""
        else:
            complex_name = str(raw_name).strip()
        if complex_name.isdigit():
            raise HTTPException(status_code=400, detail=f"복합체 #{idx} 이름은 숫자만으로 구성될 수 없습니다.")
        protein_path = (item.get("protein_path") or "").strip()
        ligand_description = (item.get("ligand_description") or "").strip()
        ligand_type = (item.get("ligand_type") or "sdf").strip()
        protein_sequence = (item.get("protein_sequence") or "").strip()

        if not complex_name:
            raise HTTPException(status_code=400, detail=f"복합체 #{idx} 이름을 입력하세요.")
        if protein_path not in relative_map:
            raise HTTPException(status_code=400, detail=f"복합체 #{idx}의 Protein 파일({protein_path})이 업로드되지 않았습니다.")
        referenced_paths.add(protein_path)

        if ligand_type == "sdf":
            if not ligand_description:
                raise HTTPException(status_code=400, detail=f"복합체 #{idx}의 Ligand 경로를 지정하세요.")
            if ligand_description not in relative_map:
                raise HTTPException(status_code=400, detail=f"복합체 #{idx}의 Ligand 파일({ligand_description})이 업로드되지 않았습니다.")
            referenced_paths.add(ligand_description)

        writer.writerow([complex_name, protein_path, ligand_description, protein_sequence])

    csv_obj = {
        "filename": "protein_ligand_info.csv",
        "data_b64": base64.b64encode(csv_buffer.getvalue().encode("utf-8")).decode("ascii"),
    }

    pdb_files = _encode_diffdock_files(relative_map, referenced_paths, {".pdb", ".cif"})
    if not pdb_files:
        raise HTTPException(status_code=400, detail="DiffDock에는 최소 1개의 Protein PDB 파일이 필요합니다.")
    sdf_files = _encode_diffdock_files(relative_map, referenced_paths, {".sdf"})

    cmd = _build_diffdock_cmd(csv_obj["filename"])
    return {
        "cmd": cmd,
        "protein_ligand_csv": csv_obj,
        "pdb_files": pdb_files,
        "sdf_files": sdf_files,
        "data_dir": DIFFDOCK_DATA_DIR,
        "inputs_dir": DIFFDOCK_INPUTS_DIR,
        "out_dir": DIFFDOCK_OUT_DIR,
        "config": DIFFDOCK_CONFIG,
        "extra_args": "",
        "cuda_visible_devices": "",
    }


def _encode_diffdock_files(relative_map: dict[str, Path], referenced_paths: set[str], suffixes: set[str]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for rel_path in sorted(referenced_paths):
        path = relative_map.get(rel_path)
        if not path:
            continue
        if path.suffix.lower() not in suffixes:
            continue
        filename = Path(rel_path).name
        if filename in seen:
            raise HTTPException(status_code=400, detail=f"파일 이름이 중복되었습니다: {filename}")
        entries.append({"filename": filename, "data_b64": file_to_base64(path)})
        seen.add(filename)
    return entries


def _build_diffdock_cmd(csv_filename: str) -> str:
    remote_csv = f"{DIFFDOCK_DATA_DIR}/{csv_filename}"
    return (
        f"python3 -m inference --config {DIFFDOCK_CONFIG} "
        f"--protein_ligand_csv {remote_csv} --out_dir {DIFFDOCK_OUT_DIR}"
    )


def _parse_fasta_entries(text: str) -> list[tuple[str | None, str]]:
    entries: list[tuple[str | None, str]] = []
    current_header: str | None = None
    current_chunks: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if current_chunks:
                entries.append((current_header, "".join(current_chunks)))
                current_chunks = []
            current_header = line[1:].strip() or None
        else:
            cleaned = line.replace(" ", "").replace("\t", "")
            if cleaned:
                current_chunks.append(cleaned)
    if current_chunks:
        entries.append((current_header, "".join(current_chunks)))
    return [(header, seq) for header, seq in entries if seq]


def _write_fasta_file(path: Path, entries: list[tuple[str | None, str]]) -> None:
    lines: list[str] = []
    for idx, (header, seq) in enumerate(entries, start=1):
        label = header or f"chain_{idx}"
        lines.append(f">{label}")
        lines.append(seq)
    content = "\n".join(lines)
    if not content.endswith("\n"):
        content += "\n"
    path.write_text(content)
