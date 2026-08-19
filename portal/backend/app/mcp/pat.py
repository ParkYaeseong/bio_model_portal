from __future__ import annotations

import hashlib
import secrets
from datetime import datetime

from sqlalchemy.orm import Session

from .. import models

TOKEN_PREFIX = "kbfpat_"


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_pat(db: Session, user: models.User, name: str) -> tuple[str, models.PersonalAccessToken]:
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = models.PersonalAccessToken(
        user_id=user.id, name=(name or "token")[:80],
        token_hash=_hash(raw), prefix=raw[: len(TOKEN_PREFIX) + 4],
    )
    db.add(row); db.commit(); db.refresh(row)
    return raw, row


def resolve_pat(db: Session, raw: str | None) -> models.User | None:
    if not raw or not raw.startswith(TOKEN_PREFIX):
        return None
    row = (
        db.query(models.PersonalAccessToken)
        .filter_by(token_hash=_hash(raw), revoked_at=None)
        .first()
    )
    if not row:
        return None
    row.last_used_at = datetime.utcnow()
    db.commit()
    return db.query(models.User).filter_by(id=row.user_id).first()


def list_pats(db: Session, user: models.User) -> list[models.PersonalAccessToken]:
    return (
        db.query(models.PersonalAccessToken)
        .filter_by(user_id=user.id, revoked_at=None)
        .order_by(models.PersonalAccessToken.created_at.desc())
        .all()
    )


def revoke_pat(db: Session, user: models.User, token_id: str) -> bool:
    row = db.query(models.PersonalAccessToken).filter_by(id=token_id, user_id=user.id).first()
    if not row or row.revoked_at is not None:
        return False
    row.revoked_at = datetime.utcnow()
    db.commit()
    return True
