from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models
from ..auth import get_current_user
from ..chat import loop as chat_loop
from ..chat.providers import ProviderError, get_provider
from ..database import get_db
from ..selfimprove import capture as si_capture

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatAttachment(BaseModel):
    name: str
    base64: str


class ChatRequest(BaseModel):
    provider: str
    api_key: str
    model: str | None = None
    messages: list[ChatMessage]
    attachments: list[ChatAttachment] = []


class ToolCall(BaseModel):
    name: str
    arguments: dict
    result: dict


class ChatResponse(BaseModel):
    reply: str
    tool_calls: list[ToolCall]
    model: str


@router.post("", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    provider = get_provider(payload.provider)
    if provider is None:
        raise HTTPException(status_code=400, detail=f"unknown provider: {payload.provider}")
    api_key = (payload.api_key or "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="API key is required.")
    history = [{"role": m.role, "content": m.content} for m in payload.messages]
    if not any(m["role"] == "user" and m["content"].strip() for m in history):
        raise HTTPException(status_code=400, detail="At least one user message is required.")

    attachments = [{"name": a.name, "base64": a.base64} for a in payload.attachments]
    if attachments:
        # Tell the LLM which files are available so it knows to call run_model;
        # the base64 payloads are injected server-side, never through its context.
        names = ", ".join(a.name for a in payload.attachments)
        for msg in reversed(history):
            if msg["role"] == "user":
                msg["content"] += (
                    f"\n\n[첨부된 파일: {names}] — run_model 도구를 호출하면 이 파일들이 "
                    "자동으로 모델 입력으로 사용됩니다."
                )
                break

    model = (payload.model or "").strip() or provider.default_model
    try:
        result = chat_loop.run_chat(db, current_user, provider, api_key, model, history, attachments=attachments)
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=f"LLM request failed: {exc}") from exc
    si_capture.record_interaction(db, current_user, payload.provider, result["model"], history, result)
    return ChatResponse(**result)
