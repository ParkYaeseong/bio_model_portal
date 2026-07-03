# tests/test_selfimprove_capture.py
import uuid
from app import models
from app.database import SessionLocal
from app.selfimprove import capture


def _user(db):
    u = models.User(username=f"cap_{uuid.uuid4().hex[:8]}", password_hash="x")
    db.add(u); db.commit(); db.refresh(u)
    return u


def test_sanitize_drops_base64_and_truncates_long_strings():
    calls = [{
        "name": "run_model",
        "arguments": {"pipeline": "proteinmpnn", "files": [{"name": "x.pdb", "base64": "AAAA"}], "note": "z" * 900},
        "result": {"ok": True, "job_id": "j1"},
    }]
    out = capture.sanitize_tool_calls(calls)
    assert out[0]["name"] == "run_model"
    assert out[0]["ok"] is True
    assert "files" not in out[0]["arguments_sanitized"]
    assert out[0]["arguments_sanitized"]["pipeline"] == "proteinmpnn"
    # long string replaced by a length marker, not the raw content
    assert out[0]["arguments_sanitized"]["note"] == "<str:900>"


def test_record_interaction_extracts_job_ids_and_lengths():
    with SessionLocal() as db:
        u = _user(db)
        history = [{"role": "user", "content": "run it"}]
        result = {
            "reply": "done",
            "tool_calls": [
                {"name": "list_models", "arguments": {}, "result": {"ok": True}},
                {"name": "run_model", "arguments": {"pipeline": "esmfold", "files": [{"name": "a", "base64": "QQ=="}]},
                 "result": {"ok": True, "job_id": "job-xyz"}},
            ],
            "model": "claude-opus-4-8",
        }
        capture.record_interaction(db, u, "anthropic", "claude-opus-4-8", history, result)
        it = db.query(models.Interaction).filter_by(user_id=u.id).first()
        assert it is not None
        assert it.user_message_len == len("run it")
        assert it.reply_len == len("done")
        assert it.job_ids == ["job-xyz"]
        names = [c["name"] for c in it.tool_calls]
        assert names == ["list_models", "run_model"]
        assert all("base64" not in (c["arguments_sanitized"].get("files") or [{}])[0] for c in it.tool_calls if c["name"] == "run_model")


def test_record_interaction_never_raises(monkeypatch):
    # A broken db must not propagate — capture is best-effort.
    class Boom:
        def add(self, *a): raise RuntimeError("db down")
    capture.record_interaction(Boom(), None, "anthropic", "m", [{"role": "user", "content": "x"}], {"reply": "y", "tool_calls": []})
    # no exception == pass
