from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models
from ..auth import get_current_user
from ..database import get_db
from ..storage import storage_path
from ..workflow import orchestrator, template_loader

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


class InstantiateRequest(BaseModel):
    template_key: str = "rapid_v1"
    name: str | None = None
    description: str | None = None


class RunRequest(BaseModel):
    sequence: str | None = None
    backbone_path: str | None = None
    step_params: dict = {}


def _wf_dict(wf: models.Workflow, last_status: str | None) -> dict:
    return {
        "id": wf.id, "name": wf.name, "description": wf.description,
        "template_key": wf.template_key, "last_run_status": last_status,
        "created_at": wf.created_at.isoformat(),
    }


@router.get("")
def list_workflows(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    wfs = db.query(models.Workflow).filter_by(owner_id=user.id).order_by(models.Workflow.created_at.desc()).all()
    out = []
    for wf in wfs:
        last = db.query(models.WorkflowRun).filter_by(workflow_id=wf.id).order_by(models.WorkflowRun.created_at.desc()).first()
        out.append(_wf_dict(wf, last.status if last else None))
    return {"workflows": out}


@router.post("")
def instantiate(payload: InstantiateRequest, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    try:
        tpl = template_loader.load_template(payload.template_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown template.")
    wf = models.Workflow(
        name=payload.name or tpl.get("name", payload.template_key),
        description=payload.description or tpl.get("description"),
        owner_id=user.id, template_key=payload.template_key, dag=tpl,
    )
    db.add(wf); db.commit(); db.refresh(wf)
    return _wf_dict(wf, None)


@router.post("/upload")
def upload_input(file: UploadFile = File(...), user: models.User = Depends(get_current_user)):
    dest_dir = storage_path("uploads", str(user.id), f"wf_{uuid4().hex}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Sanitize to a safe basename so a crafted filename can't escape dest_dir.
    raw_name = PurePosixPath(file.filename or "input").name
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", raw_name) or "input"
    dest = dest_dir / safe_name
    dest.write_bytes(file.file.read())
    return {"backbone_path": str(dest), "file_name": dest.name}


def _validate_owned_upload(backbone_path: str | None, user_id: int) -> str | None:
    """Reject client-supplied paths outside the user's own uploads tree.

    Prevents arbitrary server file reads / cross-user access via backbone_path."""
    if not backbone_path:
        return None
    uploads_root = storage_path("uploads", str(user_id)).resolve()
    try:
        resolved = Path(backbone_path).resolve()
    except (OSError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid backbone_path.")
    if not resolved.is_relative_to(uploads_root) or not resolved.is_file():
        raise HTTPException(status_code=400, detail="Invalid backbone_path.")
    return str(resolved)


@router.get("/{workflow_id}")
def get_workflow(workflow_id: str, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    wf = db.query(models.Workflow).filter_by(id=workflow_id, owner_id=user.id).first()
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    runs = db.query(models.WorkflowRun).filter_by(workflow_id=wf.id).order_by(models.WorkflowRun.created_at.desc()).all()
    return {**_wf_dict(wf, runs[0].status if runs else None), "dag": wf.dag,
            "runs": [{"id": r.id, "status": r.status, "created_at": r.created_at.isoformat()} for r in runs]}


@router.post("/{workflow_id}/runs")
def start_run(workflow_id: str, payload: RunRequest, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    wf = db.query(models.Workflow).filter_by(id=workflow_id, owner_id=user.id).first()
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    backbone_path = _validate_owned_upload(payload.backbone_path, user.id)
    run = models.WorkflowRun(
        workflow_id=wf.id, owner_id=user.id, status="queued",
        input_summary={"sequence": payload.sequence, "backbone_path": backbone_path},
    )
    db.add(run); db.commit(); db.refresh(run)
    orchestrator.start_run(db, run)
    return {"id": run.id, "status": run.status}


def _step_dict(s: models.WorkflowRunStep) -> dict:
    return {"order": s.order, "step_name": s.step_name, "worker_name": s.worker_name,
            "status": s.status, "job_id": s.job_id, "metrics": s.metrics,
            "error_message": s.error_message, "logs": s.logs}


@router.get("/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    run = db.query(models.WorkflowRun).filter_by(id=run_id, owner_id=user.id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    return {"id": run.id, "status": run.status, "error_message": run.error_message,
            "input_summary": run.input_summary, "output_summary": run.output_summary,
            "steps": [_step_dict(s) for s in run.steps]}


@router.get("/runs/{run_id}/report")
def get_report(run_id: str, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    run = db.query(models.WorkflowRun).filter_by(id=run_id, owner_id=user.id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    ctx = run.output_summary or {}
    return {"run_id": run.id, "status": run.status, "candidates": ctx.get("candidates", [])}


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    run = db.query(models.WorkflowRun).filter_by(id=run_id, owner_id=user.id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    if run.status in {"running", "queued"}:
        run.status = "cancelled"
        run.finished_at = datetime.utcnow()
        db.commit()
    return {"id": run.id, "status": run.status}
