from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models
from ..auth import get_current_user
from ..config import get_settings
from ..database import get_db

from pydantic import BaseModel

router = APIRouter(prefix="/api/assistant", tags=["assistant"])
settings = get_settings()


class AssistantRequest(BaseModel):
    job_id: str
    message: str
    artifact_id: str | None = None


class AssistantResponse(BaseModel):
    reply: str


@router.post("", response_model=AssistantResponse)
def ask_assistant(
    payload: AssistantRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if not settings.openai_api_key:
        raise HTTPException(status_code=503, detail="Assistant is not configured.")

    job = (
        db.query(models.Job)
        .filter(models.Job.id == payload.job_id, models.Job.user_id == current_user.id)
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    artifact: models.Artifact | None = None
    if payload.artifact_id:
        artifact = (
            db.query(models.Artifact)
            .filter(models.Artifact.id == payload.artifact_id, models.Artifact.job_id == job.id)
            .first()
        )
        if not artifact:
            raise HTTPException(status_code=404, detail="Artifact not found.")

    context = build_job_context(job, artifact)
    reply = request_openai(payload.message, context)
    return AssistantResponse(reply=reply)


def build_job_context(job: models.Job, artifact: models.Artifact | None) -> str:
    lines: list[str] = [
        "You are helping with a scientific computing portal.",
        f"Job Title: {job.title}",
        f"Pipeline: {job.pipeline}",
        f"Status: {job.status}",
    ]
    if job.notes:
        lines.append(f"User notes: {job.notes}")
    if job.parameters:
        try:
            param_json = json.dumps(job.parameters, ensure_ascii=False)
            lines.append(f"Parameters: {param_json}")
        except TypeError:
            pass
    if artifact:
        lines.append(
            "Artifact focus: "
            f"name={artifact.file_name}, kind={artifact.kind}, size={artifact.size_bytes} bytes"
        )

    snippet_blocks = extract_result_snippets(job)
    for title, snippet in snippet_blocks:
        lines.append(f"{title}:\n{snippet}")
    return "\n\n".join(lines)


TEXT_PRIORITY: dict[str, list[str]] = {
    "phastest": ["summary.txt", "detail.txt", "stdout.log"],
    "alphafold": ["stdout.log", "stderr.log"],
    "diffdock": ["stdout.log", "stderr.log"],
}


def extract_result_snippets(job: models.Job) -> list[tuple[str, str]]:
    base_dir = Path(job.result_dir or "")
    if not base_dir.exists():
        return []
    snippets: list[tuple[str, str]] = []
    candidates = TEXT_PRIORITY.get(job.pipeline, [])
    searched_paths: set[Path] = set()
    for name in candidates:
        for candidate in base_dir.rglob(name):
            if candidate in searched_paths:
                continue
            text = candidate.read_text(encoding="utf-8", errors="ignore").strip()
            if text:
                snippets.append((candidate.name, text[:2000]))
                searched_paths.add(candidate)
                break
        if len(snippets) >= 2:
            break
    return snippets


def request_openai(message: str, context: str) -> str:
    try:
        response = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            timeout=60,
            json={
                "model": settings.openai_model,
                "temperature": 0.2,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a Korean scientific assistant helping interpret protein folding, docking, and phage"
                            " analysis results. Provide concise, actionable guidance and mention important cautions."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Context:\n{context}\n\nUser question:\n{message}",
                    },
                ],
            },
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Assistant request failed: {exc}") from exc

    data: dict[str, Any] = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise HTTPException(status_code=502, detail="Assistant response was empty.")
    content = choices[0].get("message", {}).get("content", "").strip()
    if not content:
        raise HTTPException(status_code=502, detail="Assistant response missing content.")
    return content
