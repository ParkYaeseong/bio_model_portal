import uuid

from app import models
from app.chat import loop as chat_loop
from app.chat.providers import AnthropicProvider, get_provider
from app.database import SessionLocal


def _user(db):
    u = models.User(username=f"c_{uuid.uuid4().hex[:8]}", password_hash="x")
    db.add(u); db.commit(); db.refresh(u)
    return u


# ---- Anthropic adapter ----------------------------------------------------

def test_anthropic_format_tools_uses_input_schema():
    p = AnthropicProvider()
    from app.mcp.tools import TOOLS

    tools = p.format_tools(TOOLS)
    names = {t["name"] for t in tools}
    assert {"list_models", "run_model", "job_status"}.issubset(names)
    assert all("input_schema" in t for t in tools)


def test_anthropic_parse_splits_text_and_tool_use():
    p = AnthropicProvider()
    resp = {
        "stop_reason": "tool_use",
        "content": [
            {"type": "text", "text": "let me check"},
            {"type": "tool_use", "id": "tu_1", "name": "list_models", "input": {}},
        ],
    }
    parsed = p.parse(resp)
    assert parsed.text == "let me check"
    assert parsed.tool_calls == [{"id": "tu_1", "name": "list_models", "arguments": {}}]
    assert parsed.stop == "tool_use"


def test_anthropic_tool_result_roundtrip_shape():
    p = AnthropicProvider()
    messages: list[dict] = []
    p.append_assistant(messages, {"content": [{"type": "tool_use", "id": "tu_1", "name": "x", "input": {}}]})
    p.append_tool_results(messages, [{"id": "tu_1", "name": "x", "content": "{}"}])
    assert messages[0]["role"] == "assistant"
    assert messages[1]["role"] == "user"
    assert messages[1]["content"][0]["type"] == "tool_result"
    assert messages[1]["content"][0]["tool_use_id"] == "tu_1"


def test_provider_list_models_parsing(monkeypatch):
    from app.chat import providers

    class FakeResp:
        def __init__(self, data):
            self._d = data

        def raise_for_status(self):
            pass

        def json(self):
            return self._d

    monkeypatch.setattr(providers.httpx, "get",
        lambda *a, **k: FakeResp({"data": [{"id": "claude-opus-4-8"}, {"id": "claude-haiku-4-5"}]}))
    assert providers.AnthropicProvider().list_models("k") == ["claude-opus-4-8", "claude-haiku-4-5"]

    monkeypatch.setattr(providers.httpx, "get",
        lambda *a, **k: FakeResp({"data": [{"id": "gpt-4o"}, {"id": "text-embedding-3-small"}, {"id": "gpt-5"}]}))
    assert providers.OpenAIProvider().list_models("k") == ["gpt-4o", "gpt-5"]

    monkeypatch.setattr(providers.httpx, "get", lambda *a, **k: FakeResp({"models": [
        {"name": "models/gemini-2.0-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/embedding-001", "supportedGenerationMethods": ["embedContent"]},
    ]}))
    assert providers.GeminiProvider().list_models("k") == ["gemini-2.0-flash"]


def test_get_provider_known_and_unknown():
    assert get_provider("anthropic") is not None
    assert get_provider("openai") is not None
    assert get_provider("gemini") is not None
    assert get_provider("exaone") is not None
    assert get_provider("bogus") is None


# ---- EXAONE (self-hosted local LLM) adapter -------------------------------

def test_get_provider_exaone_alias_local():
    from app.chat.providers import ExaoneProvider

    assert isinstance(get_provider("exaone"), ExaoneProvider)
    # "local" is a friendly alias for the same provider.
    assert isinstance(get_provider("local"), ExaoneProvider)
    assert isinstance(get_provider("EXAONE"), ExaoneProvider)


def test_exaone_requires_no_api_key():
    from app.chat.providers import ExaoneProvider

    p = ExaoneProvider()
    assert p.requires_api_key is False
    # The default model is the configured served EXAONE id.
    from app.config import get_settings

    assert p.default_model == get_settings().local_llm_model


def test_exaone_list_models_hits_local_endpoint_without_key(monkeypatch):
    from app.chat import providers

    captured: dict = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"id": "LGAI-EXAONE/EXAONE-4.5-33B-AWQ"}]}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        return FakeResp()

    monkeypatch.setattr(providers.httpx, "get", fake_get)
    models_out = providers.ExaoneProvider().list_models("")
    assert models_out == ["LGAI-EXAONE/EXAONE-4.5-33B-AWQ"]
    assert captured["url"].endswith("/v1/models")
    # No Authorization header on the local path.
    assert not (captured["headers"] or {}).get("Authorization")


def test_exaone_request_no_auth_header_and_parses_tool_calls(monkeypatch):
    from app.chat import providers

    captured: dict = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "function": {"name": "list_models", "arguments": "{}"},
                                }
                            ],
                        },
                    }
                ]
            }

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        captured["json"] = kwargs.get("json")
        return FakeResp()

    monkeypatch.setattr(providers.httpx, "post", fake_post)
    p = providers.ExaoneProvider()
    resp = p.request("", p.default_model, "sys", [{"role": "user", "content": "hi"}], [])
    parsed = p.parse(resp)

    # Routed to the local /chat/completions endpoint, no Authorization header.
    assert captured["url"].endswith("/v1/chat/completions")
    assert "Authorization" not in (captured["headers"] or {})
    # Standard OpenAI tool_calls are parsed.
    assert parsed.stop == "tool_calls"
    assert parsed.tool_calls == [{"id": "call_1", "name": "list_models", "arguments": {}}]


def test_exaone_strips_think_from_reply():
    from app.chat.providers import ExaoneProvider

    resp = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": "<think>let me reason about this</think>Here is the answer.",
                    "tool_calls": [],
                },
            }
        ]
    }
    parsed = ExaoneProvider().parse(resp)
    assert parsed.text == "Here is the answer."
    assert parsed.tool_calls == []


# ---- Loop (provider-agnostic) ---------------------------------------------

class FakeProvider:
    """Round 1: call list_models. Round 2: final text using the tool result."""

    default_model = "fake-1"

    def __init__(self):
        self.calls = 0
        self.tool_result_seen = None

    def format_tools(self, tools):
        return list(tools.keys())

    def build_messages(self, history):
        return list(history)

    def request(self, api_key, model, system, messages, tools):
        self.calls += 1
        return {"round": self.calls, "messages": messages}

    def parse(self, resp):
        from app.chat.providers import ParsedTurn

        if resp["round"] == 1:
            return ParsedTurn("", [{"id": "t1", "name": "list_models", "arguments": {}}], "tool_use")
        return ParsedTurn("Here are the models.", [], "end_turn")

    def append_assistant(self, messages, resp):
        messages.append({"role": "assistant", "content": "[tool call]"})

    def append_tool_results(self, messages, results):
        self.tool_result_seen = results
        messages.append({"role": "user", "content": results[0]["content"]})


def test_run_chat_executes_tool_and_returns_reply():
    with SessionLocal() as db:
        u = _user(db)
        provider = FakeProvider()
        out = chat_loop.run_chat(
            db, u, provider, "sk-test", "fake-1",
            [{"role": "user", "content": "what models are there?"}],
        )
        assert out["reply"] == "Here are the models."
        assert len(out["tool_calls"]) == 1
        assert out["tool_calls"][0]["name"] == "list_models"
        # The tool actually ran in-process and returned portal models.
        assert out["tool_calls"][0]["result"]["ok"] is True
        assert any(m["key"] == "esmfold" for m in out["tool_calls"][0]["result"]["models"])
        assert provider.calls == 2


def test_run_chat_tool_runs_as_the_authenticated_user():
    """job_status must resolve against the passed user's own jobs only."""
    with SessionLocal() as db:
        owner, other = _user(db), _user(db)
        job = models.Job(user_id=owner.id, title="x", pipeline="esmfold", status="submitted")
        db.add(job); db.commit(); db.refresh(job)

        from app.chat.providers import ParsedTurn

        class StatusProvider(FakeProvider):
            def parse(self, resp):
                if resp["round"] == 1:
                    return ParsedTurn("", [{"id": "t1", "name": "job_status", "arguments": {"job_id": job.id}}], "tool_use")
                return ParsedTurn("done", [], "end_turn")

        # Owner sees the job.
        out_owner = chat_loop.run_chat(db, owner, StatusProvider(), "k", "fake-1", [{"role": "user", "content": "status?"}])
        assert out_owner["tool_calls"][0]["result"]["ok"] is True
        # A different user does not.
        out_other = chat_loop.run_chat(db, other, StatusProvider(), "k", "fake-1", [{"role": "user", "content": "status?"}])
        assert out_other["tool_calls"][0]["result"]["ok"] is False


def test_run_chat_injects_attachments_into_run_model(monkeypatch):
    import base64 as _b64

    from app.mcp import tools as mcp_tools
    from app.chat.providers import ParsedTurn

    captured = {}

    def fake_create(db_, *, user_id, title, pipeline, params, input_files=None, sequence=None):
        captured["input_files"] = list(input_files or [])
        j = models.Job(user_id=user_id, title=title, pipeline=pipeline, status="submitted")
        db_.add(j); db_.commit(); db_.refresh(j)
        return j

    monkeypatch.setattr(mcp_tools.job_bridge, "create_step_job", fake_create)

    class RunProvider(FakeProvider):
        def parse(self, resp):
            if resp["round"] == 1:
                # LLM hallucinates a name-only files arg — the real upload must
                # still be injected (override), not suppressed.
                return ParsedTurn(
                    "",
                    [{"id": "t1", "name": "run_model",
                      "arguments": {"pipeline": "esmfold", "files": [{"name": "seq.fasta"}]}}],
                    "tool_use",
                )
            return ParsedTurn("submitted", [], "end_turn")

    with SessionLocal() as db:
        u = _user(db)
        attachments = [{"name": "seq.fasta", "base64": _b64.b64encode(b">a\nACDEFG").decode()}]
        out = chat_loop.run_chat(
            db, u, RunProvider(), "k", "fake-1",
            [{"role": "user", "content": "이 파일로 돌려줘"}],
            attachments=attachments,
        )
        assert out["tool_calls"][0]["result"]["ok"] is True
        # The real upload reached run_model → create_step_job as an input file,
        # even though the LLM passed a name-only files arg.
        assert len(captured["input_files"]) == 1
        # The recorded tool arguments never carry the base64 payload (only the
        # LLM's own name-only entry is echoed back).
        recorded = out["tool_calls"][0]["arguments"]
        assert all("base64" not in f for f in recorded.get("files", []))


# ---- /api/chat endpoint: api_key validation per provider ------------------

def _api_client(user):
    from fastapi.testclient import TestClient
    from app.auth import get_current_user
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app), app


def test_chat_endpoint_allows_empty_key_for_exaone(monkeypatch):
    """provider=exaone must NOT require an api_key; keyed providers still do."""
    from app.chat import providers

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"finish_reason": "stop",
                                 "message": {"content": "안녕하세요.", "tool_calls": []}}]}

    monkeypatch.setattr(providers.httpx, "post", lambda url, **k: FakeResp())

    with SessionLocal() as db:
        user = _user(db)
    client, app = _api_client(user)
    try:
        # Empty api_key + exaone → allowed (reaches the model, returns a reply).
        r = client.post("/api/chat", json={"provider": "exaone", "api_key": "",
                                           "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200, r.text
        assert r.json()["reply"] == "안녕하세요."

        # A keyed provider with an empty key is still rejected (400).
        r2 = client.post("/api/chat", json={"provider": "openai", "api_key": "",
                                            "messages": [{"role": "user", "content": "hi"}]})
        assert r2.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_chat_models_endpoint_allows_empty_key_for_exaone(monkeypatch):
    from app.chat import providers

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"id": "LGAI-EXAONE/EXAONE-4.5-33B-AWQ"}]}

    monkeypatch.setattr(providers.httpx, "get", lambda url, **k: FakeResp())

    with SessionLocal() as db:
        user = _user(db)
    client, app = _api_client(user)
    try:
        r = client.post("/api/chat/models", json={"provider": "exaone", "api_key": ""})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["models"] == ["LGAI-EXAONE/EXAONE-4.5-33B-AWQ"]
        assert body["default"] == "LGAI-EXAONE/EXAONE-4.5-33B-AWQ"

        # Keyed provider without a key is still rejected.
        r2 = client.post("/api/chat/models", json={"provider": "openai", "api_key": ""})
        assert r2.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_run_chat_hits_iteration_cap():
    with SessionLocal() as db:
        u = _user(db)
        from app.chat.providers import ParsedTurn

        class LoopingProvider(FakeProvider):
            def parse(self, resp):
                return ParsedTurn("still working", [{"id": "t", "name": "list_models", "arguments": {}}], "tool_use")

        out = chat_loop.run_chat(db, u, LoopingProvider(), "k", "fake-1", [{"role": "user", "content": "go"}], max_iters=3)
        assert len(out["tool_calls"]) == 3
        assert out["reply"] == "still working"


def test_system_prompt_mentions_chaining():
    from app.chat.loop import SYSTEM_PROMPT
    assert "from_job_id" in SYSTEM_PROMPT
