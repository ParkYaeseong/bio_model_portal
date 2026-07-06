from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models
from ..auth import get_current_user
from ..config import get_settings
from ..database import get_db
from ..selfimprove.feedback import active_artifact

router = APIRouter(prefix="/api/selfimprove", tags=["selfimprove"])
settings = get_settings()

_EMPTY_PAYLOAD = {"recommended_defaults": {}, "warnings": [], "recipes": []}


def _is_admin(user: models.User) -> bool:
    allow = {u.strip() for u in (settings.selfimprove_admin_users or "").split(",") if u.strip()}
    return user.username in allow


def _require_admin(user: models.User) -> None:
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="self-improvement admin only")


def _serialize(a: models.ImprovementArtifact) -> dict:
    return {
        "id": a.id, "status": a.status, "summary": a.summary,
        "payload": a.payload, "stats": a.stats,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


@router.get("/insights")
def insights(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Read-only view of the currently-active (admin-approved) learned guidance.

    Available to any authenticated user — the payload is aggregate/anonymized
    (recommended defaults, failure warnings, recipes), the same guidance the
    chatbot already injects into its system prompt. Proposed/rejected artifacts
    and the review queue stay admin-only (see /artifacts)."""
    art = active_artifact(db)
    if art is None:
        return {"active": False, "payload": dict(_EMPTY_PAYLOAD), "summary": None, "updated_at": None}
    payload = art.payload or {}
    return {
        "active": True,
        "payload": {
            "recommended_defaults": payload.get("recommended_defaults") or {},
            "warnings": payload.get("warnings") or [],
            "recipes": payload.get("recipes") or [],
        },
        "summary": art.summary,
        "updated_at": art.created_at.isoformat() if art.created_at else None,
    }


@router.get("/admin")
def admin_status(current_user: models.User = Depends(get_current_user)):
    """Whether the caller may review/approve artifacts — drives UI link visibility."""
    return {"is_admin": _is_admin(current_user)}


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
