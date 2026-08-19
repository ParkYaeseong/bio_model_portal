from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models
from ..auth import get_current_user
from ..database import get_db
from ..mcp import pat

router = APIRouter(prefix="/api/mcp/tokens", tags=["mcp-tokens"])


class CreateTokenRequest(BaseModel):
    name: str = "token"


def _row(t: models.PersonalAccessToken) -> dict:
    return {"id": t.id, "name": t.name, "prefix": t.prefix,
            "created_at": t.created_at.isoformat(),
            "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None}


@router.post("")
def create_token(payload: CreateTokenRequest, db: Session = Depends(get_db),
                 user: models.User = Depends(get_current_user)):
    raw, row = pat.create_pat(db, user, payload.name)
    return {**_row(row), "token": raw}  # raw shown once


@router.get("")
def list_tokens(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    return {"tokens": [_row(t) for t in pat.list_pats(db, user)]}


@router.post("/{token_id}/revoke")
def revoke_token(token_id: str, db: Session = Depends(get_db),
                 user: models.User = Depends(get_current_user)):
    if not pat.revoke_pat(db, user, token_id):
        raise HTTPException(status_code=404, detail="Token not found.")
    return {"ok": True}
