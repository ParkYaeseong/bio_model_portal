from __future__ import annotations

import json

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from ..database import SessionLocal
from . import pat
from .tools import TOOLS

router = APIRouter(tags=["mcp"])
SERVER_INFO = {"name": "bio-model-portal", "version": "1.0"}
SUPPORTED_PROTOCOL = "2024-11-05"


def _err(_id, code, message):
    return {"jsonrpc": "2.0", "id": _id, "error": {"code": code, "message": message}}


def _ok(_id, result):
    return {"jsonrpc": "2.0", "id": _id, "result": result}


@router.post("/mcp")
async def mcp_endpoint(request: Request):
    auth = request.headers.get("Authorization", "")
    raw = auth[7:] if auth.lower().startswith("bearer ") else None
    with SessionLocal() as db:
        user = pat.resolve_pat(db, raw)
        if user is None:
            return JSONResponse(status_code=401, content={"error": "invalid or missing PAT"})
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(_err(None, -32700, "parse error"))
        if not isinstance(body, dict):
            return JSONResponse(_err(None, -32600, "invalid request (expected a JSON object)"))
        _id = body.get("id")
        method = body.get("method")
        params = body.get("params") if isinstance(body.get("params"), dict) else {}

        # Notifications (no id, or notifications/*) get no response body per JSON-RPC.
        if method and str(method).startswith("notifications/"):
            return Response(status_code=202)

        if method == "initialize":
            requested = params.get("protocolVersion")
            return JSONResponse(_ok(_id, {
                "protocolVersion": requested if isinstance(requested, str) and requested else SUPPORTED_PROTOCOL,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            }))
        if method == "tools/list":
            tools = [
                {"name": name, "description": desc, "inputSchema": schema}
                for name, (_fn, desc, schema) in TOOLS.items()
            ]
            return JSONResponse(_ok(_id, {"tools": tools}))
        if method == "tools/call":
            name = params.get("name")
            entry = TOOLS.get(name)
            if not entry:
                return JSONResponse(_ok(_id, {
                    "content": [{"type": "text", "text": f"unknown tool: {name}"}],
                    "isError": True,
                }))
            fn = entry[0]
            try:
                result = fn(db, user, params.get("arguments") or {})
                is_error = isinstance(result, dict) and result.get("ok") is False
            except Exception as exc:  # noqa: BLE001
                result, is_error = {"ok": False, "error": str(exc)}, True
            return JSONResponse(_ok(_id, {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                "isError": is_error,
            }))
        return JSONResponse(_err(_id, -32601, f"method not found: {method}"))
