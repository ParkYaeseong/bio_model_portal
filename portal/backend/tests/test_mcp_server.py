import uuid

from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app
from app.mcp import pat


def _pat():
    with SessionLocal() as db:
        u = models.User(username=f"m_{uuid.uuid4().hex[:8]}", password_hash="x")
        db.add(u); db.commit(); db.refresh(u)
        raw, _ = pat.create_pat(db, u, "cli")
        return raw


def _rpc(client, raw, method, params=None, _id=1):
    return client.post("/mcp", headers={"Authorization": f"Bearer {raw}"},
                       json={"jsonrpc": "2.0", "id": _id, "method": method, "params": params or {}})


def test_requires_valid_pat():
    client = TestClient(app)
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert r.status_code == 401


def test_initialize_and_tools_list():
    client = TestClient(app)
    raw = _pat()
    assert _rpc(client, raw, "initialize").json()["result"]["serverInfo"]["name"] == "bio-model-portal"
    tools = _rpc(client, raw, "tools/list").json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"list_models", "run_model", "run_chain", "job_status", "job_result", "cancel_job"} == names
    assert all("inputSchema" in t for t in tools)


def test_tools_call_list_models():
    client = TestClient(app)
    raw = _pat()
    r = _rpc(client, raw, "tools/call", {"name": "list_models", "arguments": {}}).json()
    text = r["result"]["content"][0]["text"]
    assert "proteinmpnn" in text


def test_unknown_method_returns_jsonrpc_error():
    client = TestClient(app)
    raw = _pat()
    r = _rpc(client, raw, "no/such").json()
    assert r["error"]["code"] == -32601


def test_non_dict_body_returns_invalid_request():
    client = TestClient(app)
    raw = _pat()
    r = client.post("/mcp", headers={"Authorization": f"Bearer {raw}"}, json=[1, 2, 3])
    assert r.status_code == 200 and r.json()["error"]["code"] == -32600


def test_notification_gets_202_no_body():
    client = TestClient(app)
    raw = _pat()
    r = client.post("/mcp", headers={"Authorization": f"Bearer {raw}"},
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert r.status_code == 202 and r.content == b""


def test_initialize_echoes_requested_protocol():
    client = TestClient(app)
    raw = _pat()
    r = _rpc(client, raw, "initialize", {"protocolVersion": "2025-06-18"}).json()
    assert r["result"]["protocolVersion"] == "2025-06-18"
