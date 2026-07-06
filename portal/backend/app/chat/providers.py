"""Provider adapters for the in-UI execution chatbot.

Each adapter exposes a uniform interface that ``loop.run_chat`` drives, so the
tool-calling loop is written once and works across Anthropic, OpenAI, and
Gemini. Anthropic is the priority path (live-verified); OpenAI and Gemini are
implemented structurally.

We call each provider over raw HTTP with ``httpx`` — matching the existing
``routers/assistant.py`` OpenAI call and keeping the three providers uniform
(the alternative, the Anthropic SDK for one and httpx for the other two, would
be inconsistent for a single provider abstraction).
"""
from __future__ import annotations

import json
from typing import Any

import httpx

_TIMEOUT = 120
_MAX_TOKENS = 2048


class ProviderError(Exception):
    """Raised when an upstream LLM call fails (surfaced as HTTP 502)."""


class ParsedTurn:
    __slots__ = ("text", "tool_calls", "stop")

    def __init__(self, text: str, tool_calls: list[dict], stop: str) -> None:
        self.text = text
        self.tool_calls = tool_calls  # [{"id", "name", "arguments"}]
        self.stop = stop


class Provider:
    """Interface implemented by each provider adapter."""

    name: str = ""
    default_model: str = ""

    def list_models(self, api_key: str) -> list[str]:  # pragma: no cover - interface
        raise NotImplementedError

    def format_tools(self, tools: dict) -> list[dict]:  # pragma: no cover - interface
        raise NotImplementedError

    def build_messages(self, history: list[dict]) -> list[dict]:  # pragma: no cover
        raise NotImplementedError

    def request(self, api_key: str, model: str, system: str, messages: list[dict], tools: list[dict]) -> dict:  # pragma: no cover
        raise NotImplementedError

    def parse(self, resp: dict) -> ParsedTurn:  # pragma: no cover - interface
        raise NotImplementedError

    def append_assistant(self, messages: list[dict], resp: dict) -> None:  # pragma: no cover
        raise NotImplementedError

    def append_tool_results(self, messages: list[dict], results: list[dict]) -> None:  # pragma: no cover
        raise NotImplementedError


def _clean_history(history: list[dict]) -> list[dict]:
    """Keep only user/assistant text turns; the loop appends tool turns itself."""
    out: list[dict] = []
    for m in history or []:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            out.append({"role": role, "content": content})
    return out


class AnthropicProvider(Provider):
    name = "anthropic"
    default_model = "claude-opus-4-8"

    def list_models(self, api_key: str) -> list[str]:
        try:
            resp = httpx.get(
                "https://api.anthropic.com/v1/models",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                params={"limit": 1000},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(_http_detail(exc)) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"connection error ({type(exc).__name__})") from exc
        return [m["id"] for m in (resp.json().get("data") or []) if m.get("id")]

    def format_tools(self, tools: dict) -> list[dict]:
        return [
            {"name": name, "description": desc, "input_schema": schema}
            for name, (_fn, desc, schema) in tools.items()
        ]

    def build_messages(self, history: list[dict]) -> list[dict]:
        # Anthropic accepts string content for plain text turns.
        return [dict(m) for m in _clean_history(history)]

    def request(self, api_key: str, model: str, system: str, messages: list[dict], tools: list[dict]) -> dict:
        try:
            resp = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                timeout=_TIMEOUT,
                json={
                    "model": model,
                    "max_tokens": _MAX_TOKENS,
                    "system": system,
                    "tools": tools,
                    "messages": messages,
                },
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(_http_detail(exc)) from exc
        except httpx.HTTPError as exc:
            # Use the exception class name only — str(exc) can embed the request
            # URL/headers, which we never want echoed back to the caller.
            raise ProviderError(f"connection error ({type(exc).__name__})") from exc
        return resp.json()

    def parse(self, resp: dict) -> ParsedTurn:
        texts: list[str] = []
        tool_calls: list[dict] = []
        for block in resp.get("content") or []:
            if block.get("type") == "text":
                texts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    {"id": block.get("id"), "name": block.get("name"), "arguments": block.get("input") or {}}
                )
        return ParsedTurn("".join(texts).strip(), tool_calls, resp.get("stop_reason") or "")

    def append_assistant(self, messages: list[dict], resp: dict) -> None:
        messages.append({"role": "assistant", "content": resp.get("content") or []})

    def append_tool_results(self, messages: list[dict], results: list[dict]) -> None:
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": r["id"], "content": r["content"]}
                    for r in results
                ],
            }
        )


class OpenAIProvider(Provider):
    name = "openai"
    default_model = "gpt-4o"

    # /v1/models has no capability field; exclude obvious non-chat families.
    _NON_CHAT = ("embedding", "whisper", "tts", "dall-e", "moderation",
                 "audio", "image", "realtime", "transcribe", "search", "davinci", "babbage")

    def list_models(self, api_key: str) -> list[str]:
        try:
            resp = httpx.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(_http_detail(exc)) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"connection error ({type(exc).__name__})") from exc
        ids = [m["id"] for m in (resp.json().get("data") or []) if m.get("id")]
        return sorted(i for i in ids if not any(x in i for x in self._NON_CHAT))

    def format_tools(self, tools: dict) -> list[dict]:
        return [
            {"type": "function", "function": {"name": name, "description": desc, "parameters": schema}}
            for name, (_fn, desc, schema) in tools.items()
        ]

    def build_messages(self, history: list[dict]) -> list[dict]:
        return [dict(m) for m in _clean_history(history)]

    def request(self, api_key: str, model: str, system: str, messages: list[dict], tools: list[dict]) -> dict:
        payload_messages = [{"role": "system", "content": system}] + messages
        try:
            resp = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                timeout=_TIMEOUT,
                json={"model": model, "messages": payload_messages, "tools": tools},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(_http_detail(exc)) from exc
        except httpx.HTTPError as exc:
            # Use the exception class name only — str(exc) can embed the request
            # URL/headers, which we never want echoed back to the caller.
            raise ProviderError(f"connection error ({type(exc).__name__})") from exc
        return resp.json()

    def parse(self, resp: dict) -> ParsedTurn:
        choices = resp.get("choices") or []
        if not choices:
            return ParsedTurn("", [], "")
        message = choices[0].get("message") or {}
        tool_calls = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append({"id": tc.get("id"), "name": fn.get("name"), "arguments": args})
        return ParsedTurn((message.get("content") or "").strip(), tool_calls, choices[0].get("finish_reason") or "")

    def append_assistant(self, messages: list[dict], resp: dict) -> None:
        message = (resp.get("choices") or [{}])[0].get("message") or {}
        messages.append(message)

    def append_tool_results(self, messages: list[dict], results: list[dict]) -> None:
        for r in results:
            messages.append({"role": "tool", "tool_call_id": r["id"], "content": r["content"]})


class GeminiProvider(Provider):
    name = "gemini"
    default_model = "gemini-2.0-flash"

    def list_models(self, api_key: str) -> list[str]:
        try:
            resp = httpx.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                headers={"x-goog-api-key": api_key},
                params={"pageSize": 1000},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(_http_detail(exc)) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"connection error ({type(exc).__name__})") from exc
        out = []
        for m in (resp.json().get("models") or []):
            if "generateContent" in (m.get("supportedGenerationMethods") or []):
                name = m.get("name") or ""
                out.append(name.split("/", 1)[1] if name.startswith("models/") else name)
        return sorted(out)

    def format_tools(self, tools: dict) -> list[dict]:
        return [
            {
                "function_declarations": [
                    {"name": name, "description": desc, "parameters": schema}
                    for name, (_fn, desc, schema) in tools.items()
                ]
            }
        ]

    def build_messages(self, history: list[dict]) -> list[dict]:
        out = []
        for m in _clean_history(history):
            role = "model" if m["role"] == "assistant" else "user"
            out.append({"role": role, "parts": [{"text": m["content"]}]})
        return out

    def request(self, api_key: str, model: str, system: str, messages: list[dict], tools: list[dict]) -> dict:
        # Send the key in a header, not the query string — keys in URLs leak
        # into proxy/server access logs.
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            resp = httpx.post(
                url,
                headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
                timeout=_TIMEOUT,
                json={
                    "systemInstruction": {"parts": [{"text": system}]},
                    "tools": tools,
                    "contents": messages,
                },
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(_http_detail(exc)) from exc
        except httpx.HTTPError as exc:
            # Use the exception class name only — str(exc) can embed the request
            # URL/headers, which we never want echoed back to the caller.
            raise ProviderError(f"connection error ({type(exc).__name__})") from exc
        return resp.json()

    def parse(self, resp: dict) -> ParsedTurn:
        candidates = resp.get("candidates") or []
        if not candidates:
            return ParsedTurn("", [], "")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        texts: list[str] = []
        tool_calls: list[dict] = []
        for part in parts:
            if "text" in part:
                texts.append(part.get("text", ""))
            elif "functionCall" in part:
                fc = part["functionCall"]
                name = fc.get("name")
                # Gemini function calls carry no id; key results back by name.
                tool_calls.append({"id": name, "name": name, "arguments": fc.get("args") or {}})
        return ParsedTurn("".join(texts).strip(), tool_calls, candidates[0].get("finishReason") or "")

    def append_assistant(self, messages: list[dict], resp: dict) -> None:
        content = (resp.get("candidates") or [{}])[0].get("content") or {"role": "model", "parts": []}
        messages.append(content)

    def append_tool_results(self, messages: list[dict], results: list[dict]) -> None:
        messages.append(
            {
                "role": "user",
                "parts": [
                    {"functionResponse": {"name": r["name"], "response": {"result": r["content"]}}}
                    for r in results
                ],
            }
        )


_PROVIDERS: dict[str, Provider] = {
    "anthropic": AnthropicProvider(),
    "openai": OpenAIProvider(),
    "gemini": GeminiProvider(),
}


def get_provider(name: str) -> Provider | None:
    return _PROVIDERS.get((name or "").lower())


def _http_detail(exc: httpx.HTTPStatusError) -> str:
    try:
        body = exc.response.json()
        msg = body.get("error", {})
        if isinstance(msg, dict):
            msg = msg.get("message") or json.dumps(body)
        return f"{exc.response.status_code}: {msg}"
    except Exception:  # noqa: BLE001
        return f"{exc.response.status_code}: {exc.response.text[:300]}"
