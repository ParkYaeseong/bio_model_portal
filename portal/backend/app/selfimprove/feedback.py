from __future__ import annotations

import json

from sqlalchemy.orm import Session

from .. import models


def active_artifact(db: Session) -> models.ImprovementArtifact | None:
    return (
        db.query(models.ImprovementArtifact)
        .filter_by(status="active")
        .order_by(models.ImprovementArtifact.created_at.desc())
        .first()
    )


def render_prompt_block(artifact: models.ImprovementArtifact | None) -> str:
    """Compact text injected into the chat system prompt. Empty if no artifact."""
    if artifact is None:
        return ""
    payload = artifact.payload or {}
    lines: list[str] = []
    defaults = payload.get("recommended_defaults") or {}
    if defaults:
        lines.append("Recommended defaults learned from prior successful runs:")
        for pipeline, params in defaults.items():
            lines.append(f"- {pipeline}: {json.dumps(params, ensure_ascii=False)}")
    warnings = payload.get("warnings") or []
    if warnings:
        lines.append("Known failure patterns to avoid or warn the user about:")
        for w in warnings:
            lines.append(f"- {w.get('pipeline')}: {w.get('message')} ({w.get('condition')})")
    recipes = payload.get("recipes") or []
    if recipes:
        lines.append("Common successful multi-step recipes:")
        for r in recipes:
            lines.append(f"- {r.get('goal')}")
    if not lines:
        return ""
    return (
        "\n\n[Learned guidance — aggregate stats from prior runs across the portal; "
        "use as hints, still verify with tools]\n" + "\n".join(lines)
    )
