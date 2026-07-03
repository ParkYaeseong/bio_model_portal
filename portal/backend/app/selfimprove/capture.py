from __future__ import annotations

from sqlalchemy.orm import Session

from .. import models

_MAX_STR = 512


def _sanitize_value(v):
    if isinstance(v, str):
        return v if len(v) <= _MAX_STR else f"<str:{len(v)}>"
    if isinstance(v, list):
        return [_sanitize_value(x) for x in v]
    if isinstance(v, dict):
        return {k: _sanitize_value(x) for k, x in v.items() if k not in ("base64", "files")}
    return v


def sanitize_tool_calls(tool_calls: list[dict]) -> list[dict]:
    """Strip file base64 and truncate long strings; keep name/args/ok only."""
    out = []
    for c in tool_calls or []:
        result = c.get("result") or {}
        out.append({
            "name": c.get("name"),
            "arguments_sanitized": _sanitize_value(c.get("arguments") or {}),
            "ok": result.get("ok") is not False,
        })
    return out


def _job_ids(tool_calls: list[dict]) -> list[str]:
    ids = []
    for c in tool_calls or []:
        if c.get("name") == "run_model":
            jid = (c.get("result") or {}).get("job_id")
            if jid:
                ids.append(jid)
    return ids


def record_interaction(db: Session, user, provider: str, model: str, history: list[dict], result: dict) -> None:
    """Best-effort: record one assistant turn. Never raises."""
    try:
        last_user = ""
        for m in reversed(history or []):
            if m.get("role") == "user":
                last_user = m.get("content") or ""
                break
        tool_calls = result.get("tool_calls") or []
        it = models.Interaction(
            user_id=getattr(user, "id", None),
            provider=provider,
            model=model,
            user_message_len=len(last_user),
            reply_len=len(result.get("reply") or ""),
            tool_calls=sanitize_tool_calls(tool_calls),
            job_ids=_job_ids(tool_calls),
            error=None,
        )
        db.add(it)
        db.commit()
    except Exception as exc:  # noqa: BLE001 — capture must never break chat
        print(f"[selfimprove] capture failed: {exc}")
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
