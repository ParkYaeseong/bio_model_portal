from __future__ import annotations
import base64
import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from ..auth import get_current_user
from ..config import get_settings

router = APIRouter(prefix="/api/rfdiffusion", tags=["rfdiffusion"])


def _gateway_base() -> str:
    settings = get_settings()
    base = str(settings.runpod_base).rstrip("/")
    return base[:-3].rstrip("/") if base.endswith("/v2") else base


@router.post("/contig-suggestions")
async def contig_suggestions(file: UploadFile = File(...), current_user=Depends(get_current_user)):
    data = await file.read()
    b64 = base64.b64encode(data).decode("ascii")
    url = f"{_gateway_base()}/prep/contig-suggestions"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, json={"pdb_base64": b64})
            r.raise_for_status()
            return r.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"contig suggestion failed: {exc}")
