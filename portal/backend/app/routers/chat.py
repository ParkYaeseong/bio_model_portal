from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models
from ..auth import get_current_user
from ..chat import loop as chat_loop
from ..chat.providers import ProviderError, get_provider
from ..database import get_db

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    provider: str
    api_key: str
    model: str | None = None
    messages: list[ChatMessage]


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

    model = (payload.model or "").strip() or provider.default_model
    try:
        result = chat_loop.run_chat(db, current_user, provider, api_key, model, history)
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=f"LLM request failed: {exc}") from exc
    return ChatResponse(**result)
