"""Antigen validation: score a structure on interface confidence.

Scoring is sub-second CPU work, so this proxies the gateway synchronously and
returns the metrics directly rather than creating a Job row and an archive the
caller would then have to unpack.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import get_current_user
from ..config import get_settings

router = APIRouter(prefix="/api/binder-score", tags=["binder-score"])

ENDPOINT_ID = "binder-score-local"
_POLL_INTERVAL_S = 0.4
_POLL_TIMEOUT_S = 120.0

# Mirrors pipeline_mcp.binder_score.GATEABLE_METRICS. Kept here so a bad metric
# is rejected before a request leaves the portal.
METRICS: dict[str, bool] = {
    "ipsae": True,
    "ipsae_d0chn": True,
    "ipsae_d0dom": True,
    "iptm_pae": True,
    "iptm_af2": True,
    "pdockq": True,
    "pdockq2": True,
    "lis": True,
    "interface_plddt": True,
    "plddt_mean": True,
    "epitope_plddt": True,
    "pae_interaction": False,
    "epitope_rmsd": False,
}


class Candidate(BaseModel):
    id: str | None = None
    structure: str = Field(..., description="PDB or mmCIF text of the folded candidate")
    scores: dict[str, Any] | None = Field(
        default=None,
        description="AF2/ColabFold scores JSON; must carry `pae` for interface metrics",
    )


class ScoreRequest(BaseModel):
    candidates: list[Candidate] = Field(..., min_length=1, max_length=200)
    reference_structure: str | None = Field(
        default=None, description="Reference antigen, for epitope backbone RMSD"
    )
    epitope: str | None = Field(
        default=None, description='Fixed segments, e.g. "A:12-18,A:45-52"'
    )
    primary_metric: str = "ipsae"
    cutoff: float | None = None
    pae_cutoff: float = 15.0
    dist_cutoff: float = 15.0


def _gateway_base() -> str:
    base = str(get_settings().runpod_base).rstrip("/")
    return base if base.endswith("/v2") else f"{base}/v2"


def _auth_headers() -> dict[str, str]:
    token = str(getattr(get_settings(), "runpod_api_key", "") or "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


@router.get("/metrics")
def list_metrics(current_user=Depends(get_current_user)) -> dict[str, Any]:
    """Selectable metrics and whether higher is better, for the UI."""
    return {
        "metrics": [
            {"name": name, "higher_is_better": higher}
            for name, higher in METRICS.items()
        ],
        "default": "ipsae",
    }


@router.post("/score")
async def score(req: ScoreRequest, current_user=Depends(get_current_user)) -> dict[str, Any]:
    if req.primary_metric not in METRICS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown metric {req.primary_metric!r}; expected one of {sorted(METRICS)}",
        )

    payload: dict[str, Any] = {
        "candidates": [
            {
                "id": c.id or f"candidate_{i + 1}",
                "structure": c.structure,
                **({"scores": c.scores} if c.scores else {}),
            }
            for i, c in enumerate(req.candidates)
        ],
        "primary_metric": req.primary_metric,
        "pae_cutoff": req.pae_cutoff,
        "dist_cutoff": req.dist_cutoff,
    }
    if req.reference_structure:
        payload["reference_structure"] = req.reference_structure
    if req.epitope:
        payload["epitope"] = req.epitope
    if req.cutoff is not None:
        payload["cutoff"] = req.cutoff

    base = _gateway_base()
    headers = _auth_headers()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            submitted = await client.post(
                f"{base}/{ENDPOINT_ID}/run", json={"input": payload}, headers=headers
            )
            submitted.raise_for_status()
            job_id = str((submitted.json() or {}).get("id") or "").strip()
            if not job_id:
                raise HTTPException(502, "gateway did not return a job id")

            waited = 0.0
            while waited < _POLL_TIMEOUT_S:
                await asyncio.sleep(_POLL_INTERVAL_S)
                waited += _POLL_INTERVAL_S
                polled = await client.get(
                    f"{base}/{ENDPOINT_ID}/status/{job_id}", headers=headers
                )
                polled.raise_for_status()
                body = polled.json() or {}
                state = str(body.get("status") or "").upper()
                if state == "COMPLETED":
                    # The gateway packages output into an archive but keeps the
                    # worker's own response under `raw`, which is what we want.
                    output = body.get("output") or {}
                    raw = output.get("raw") if isinstance(output, dict) else None
                    return raw if isinstance(raw, dict) else output
                if state in {"FAILED", "ERROR", "CANCELLED"}:
                    raise HTTPException(
                        502, f"scoring {state.lower()}: {body.get('error') or 'no detail'}"
                    )
            raise HTTPException(504, f"scoring did not finish within {_POLL_TIMEOUT_S:.0f}s")
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        # The worker port needs an ACG rule; an unreachable gateway is the most
        # likely cause here, so say so instead of only surfacing a socket error.
        raise HTTPException(
            502,
            f"binder-score worker unreachable via the gateway ({exc}). "
            "Check that inbound TCP 18108 is allowed on the worker host.",
        )
