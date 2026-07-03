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


def test_get_provider_known_and_unknown():
    assert get_provider("anthropic") is not None
    assert get_provider("openai") is not None
    assert get_provider("gemini") is not None
    assert get_provider("bogus") is None


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
                return ParsedTurn("", [{"id": "t1", "name": "run_model", "arguments": {"pipeline": "esmfold"}}], "tool_use")
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
        # The attached file reached run_model → create_step_job as an input file.
        assert len(captured["input_files"]) == 1
        # The recorded tool arguments do NOT contain the base64 payload.
        assert "files" not in out["tool_calls"][0]["arguments"]


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
