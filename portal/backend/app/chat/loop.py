"""Server-side tool-calling loop for the in-UI execution chatbot.

Gives the LLM the SP2 MCP tool schemas and executes any tool the LLM calls
in-process for the authenticated SSO user (no PAT) — identical isolation to the
MCP path, since it reuses ``app/mcp/tools.TOOLS`` whose functions enforce
per-user ownership.
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from .. import models
from ..mcp.tools import TOOLS
from .providers import Provider, ProviderError  # noqa: F401  (re-exported for callers)

SYSTEM_PROMPT = (
    "You are the Bio Model Portal assistant. You help users run and interpret "
    "the portal's protein models (folding, docking, design, phage analysis). "
    "You can call tools to list models, run a model, check job status, fetch a "
    "job's results, and cancel a job. Call list_models first when you are unsure "
    "of a model's key or parameters. After running or fetching results, explain "
    "them clearly and concisely in the user's language. Never fabricate job IDs "
    "or results — always use the tools."
)

MAX_ITERS = 6


def _execute_tool(db: Session, user: models.User, name: str, arguments: dict) -> dict:
    entry = TOOLS.get(name)
    if not entry:
        return {"ok": False, "error": f"unknown tool: {name}"}
    try:
        return entry[0](db, user, arguments or {})
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def run_chat(
    db: Session,
    user: models.User,
    provider: Provider,
    api_key: str,
    model: str,
    history: list[dict],
    max_iters: int = MAX_ITERS,
) -> dict:
    """Run the tool-calling loop and return {reply, tool_calls, model}."""
    tools = provider.format_tools(TOOLS)
    messages = provider.build_messages(history)
    collected: list[dict] = []
    last_text = ""

    for _ in range(max_iters):
        resp = provider.request(api_key, model, SYSTEM_PROMPT, messages, tools)
        parsed = provider.parse(resp)
        if parsed.text:
            last_text = parsed.text
        if not parsed.tool_calls:
            return {"reply": parsed.text, "tool_calls": collected, "model": model}

        provider.append_assistant(messages, resp)
        results: list[dict] = []
        for call in parsed.tool_calls:
            result = _execute_tool(db, user, call["name"], call["arguments"])
            collected.append({"name": call["name"], "arguments": call["arguments"], "result": result})
            results.append({"id": call["id"], "name": call["name"], "content": json.dumps(result, ensure_ascii=False)})
        provider.append_tool_results(messages, results)

    # Hit the iteration cap while still calling tools.
    reply = last_text or "도구 호출이 한도에 도달했습니다. 요청을 더 작게 나눠 다시 시도해 주세요."
    return {"reply": reply, "tool_calls": collected, "model": model}
