from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models
from ..auth import get_current_user
from ..config import get_settings
from ..database import get_db

router = APIRouter(prefix="/api/selfimprove", tags=["selfimprove"])
settings = get_settings()


def _require_admin(user: models.User) -> None:
    allow = {u.strip() for u in (settings.selfimprove_admin_users or "").split(",") if u.strip()}
    if user.username not in allow:
        raise HTTPException(status_code=403, detail="self-improvement admin only")


def _serialize(a: models.ImprovementArtifact) -> dict:
    return {
        "id": a.id, "status": a.status, "summary": a.summary,
        "payload": a.payload, "stats": a.stats,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


@router.get("/artifacts")
def list_artifacts(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    _require_admin(current_user)
    rows = db.query(models.ImprovementArtifact).order_by(models.ImprovementArtifact.created_at.desc()).all()
    return {"artifacts": [_serialize(a) for a in rows]}


@router.post("/artifacts/{artifact_id}/activate")
def activate(artifact_id: str, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    _require_admin(current_user)
    art = db.query(models.ImprovementArtifact).filter_by(id=artifact_id).first()
    if not art:
        raise HTTPException(status_code=404, detail="artifact not found")
    for other in db.query(models.ImprovementArtifact).filter_by(status="active").all():
        other.status = "rejected"
    art.status = "active"
    db.commit()
    return {"ok": True, "id": art.id, "status": art.status}


@router.post("/artifacts/{artifact_id}/reject")
def reject(artifact_id: str, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    _require_admin(current_user)
    art = db.query(models.ImprovementArtifact).filter_by(id=artifact_id).first()
    if not art:
        raise HTTPException(status_code=404, detail="artifact not found")
    art.status = "rejected"
    db.commit()
    return {"ok": True, "id": art.id, "status": art.status}
