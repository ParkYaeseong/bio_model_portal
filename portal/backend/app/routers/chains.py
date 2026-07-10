from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import chaining, models
from ..auth import get_current_user

router = APIRouter(prefix="/api/chains", tags=["chains"])


@router.get("/compat")
def get_compat(current_user: models.User = Depends(get_current_user)) -> dict:
    """Return the model compatibility graph + curated example chains."""
    return chaining.compat_graph()
