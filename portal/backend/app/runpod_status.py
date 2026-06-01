from __future__ import annotations

from typing import Any, Tuple


def extract_status(response: dict[str, Any]) -> Tuple[str | None, str | None]:
    """
    Normalize RunPod status payloads.
    Returns (status_text, detail_message).
    """
    raw_status = response.get("status") or response.get("state")
    detail: str | None = None
    status_text: str | None = None
    if isinstance(raw_status, dict):
        detail = raw_status.get("message") or raw_status.get("reason") or raw_status.get("details")
        for key in ("status", "state", "phase", "condition"):
            candidate = raw_status.get(key)
            if isinstance(candidate, str) and candidate.strip():
                status_text = candidate.strip()
                break
    elif isinstance(raw_status, str):
        status_text = raw_status.strip()
    return status_text, detail
