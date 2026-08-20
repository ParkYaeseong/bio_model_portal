from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models
from ..auth import get_current_user
from ..database import get_db

router = APIRouter(prefix="/api/admin", tags=["admin"])


def require_admin(current_user: models.User = Depends(get_current_user)) -> models.User:
    """Gate every cross-user view on the Keycloak `kbf-admin` realm role.

    `is_admin` is set by auth.get_current_user from the X-KBF-Admin header,
    which the backend only trusts once the gateway shared secret checks out
    -- except in the dev/test opt-in (KBF_ALLOW_INSECURE_SSO_HEADER with no
    secret configured), where the header is trusted with no secret at all.
    See auth.get_current_user for the exact trust boundary.
    """
    if not getattr(current_user, "is_admin", False):
        raise HTTPException(status_code=403, detail="admin only")
    return current_user


@router.get("/me")
def me(current_user: models.User = Depends(get_current_user)) -> dict:
    """Deliberately open to every authenticated user (not just admins): the
    frontend asks this to decide whether to render the admin menu at all. An
    unauthenticated request still gets 401 from get_current_user."""
    return {"is_admin": bool(getattr(current_user, "is_admin", False))}


@router.get("/activity")
def activity(db: Session = Depends(get_db), _: models.User = Depends(require_admin)) -> dict:
    return {"items": []}
