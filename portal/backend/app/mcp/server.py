from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from fastapi.responses import JSONResponse

from ..database import SessionLocal
from . import files as mcp_files
from . import pat
from .tools import TOOLS

router = APIRouter(tags=["mcp"])
SERVER_INFO = {"name": "bio-model-portal", "version": "1.0"}
SUPPORTED_PROTOCOL = "2024-11-05"


def _resolve_bearer(request: Request, db):
    auth = request.headers.get("Authorization", "")
    raw = auth[7:] if auth.lower().startswith("bearer ") else None
    return pat.resolve_pat(db, raw)


def _err(_id, code, message):
    return {"jsonrpc": "2.0", "id": _id, "error": {"code": code, "message": message}}


def _ok(_id, result):
    return {"jsonrpc": "2.0", "id": _id, "result": result}


@router.post("/mcp/files")
async def upload_files(
    request: Request,
    files: list[UploadFile] = File(default_factory=list),
    name: str | None = Form(default=None),
    append: bool = Form(default=False),
):
    """Upload input files with a plain HTTP request instead of a tool call.

    An MCP client on the user's own laptop cannot hand the server a path, and
    base64-ing a structure through a tool call costs the model ~75k tokens for a
    227 KB PDB (and chunking splits that cost without lowering it). This is the
    channel for that case: one `curl -F file=@x.pdb` authenticated by the same
    PAT, then reference the returned file_id in run_model.
    """
    with SessionLocal() as db:
        user = _resolve_bearer(request, db)
        if user is None:
            return JSONResponse(status_code=401, content={"ok": False, "error": "invalid or missing PAT"})

        stored: list[dict] = []
        try:
            if files:
                for upload in files:
                    async def _chunks(handle=upload):
                        while chunk := await handle.read(1024 * 1024):
                            yield chunk
                    stored.append(await mcp_files.write_workspace_stream(
                        user, name or upload.filename or "upload", _chunks(), append=append))
                    name = None  # an explicit name applies to the first file only
            else:
                # Raw-body form: curl --data-binary @x.pdb '.../mcp/files?name=x.pdb'
                raw_name = name or request.query_params.get("name")
                if not raw_name:
                    return JSONResponse(status_code=400, content={
                        "ok": False,
                        "error": "send a multipart file field (-F 'file=@x.pdb') or a raw "
                                 "body with ?name=<file name>",
                    })
                append_flag = append or request.query_params.get("append") in {"1", "true", "yes"}
                stored.append(await mcp_files.write_workspace_stream(
                    user, raw_name, request.stream(), append=append_flag))
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})

        return {
            "ok": True,
            "files": stored,
            "usage": "reference these in run_model as "
                     'files=[{"file_id": "<file_id>"}] (compare sha256 to verify).',
        }


@router.get("/mcp/files")
async def list_uploaded_files(request: Request):
    """List what this PAT has staged, for a curl-only client."""
    with SessionLocal() as db:
        user = _resolve_bearer(request, db)
        if user is None:
            return JSONResponse(status_code=401, content={"ok": False, "error": "invalid or missing PAT"})
        return {"ok": True, "workspace_dir": str(mcp_files.workspace_dir(user)),
                "files": mcp_files.list_workspace_files(user)}


@router.post("/mcp")
async def mcp_endpoint(request: Request):
    with SessionLocal() as db:
        user = _resolve_bearer(request, db)
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
