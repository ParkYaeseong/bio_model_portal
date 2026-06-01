#!/usr/bin/env python3
"""ESMFold2 worker — proxies sequence requests to the Biohub Forge API.

Unlike the other workers in this directory, this one does NOT run a local model.
It forwards requests to https://biohub.ai using the `esm` SDK and the operator's
BIOHUB_API_KEY environment variable.

Returns a payload compatible with the rest of the workers:
  {"id", "status", "output": {"pdb": "...", "sequence": "...", ...}}
"""
from __future__ import annotations

import argparse
import json
import os
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:
    from http_worker_jobs import JobManager, job_id_from_request
except ModuleNotFoundError:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from http_worker_jobs import JobManager, job_id_from_request


JOBS = JobManager()

_CLIENT_CACHE: dict[tuple[str, str], Any] = {}


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _resolve_token(payload: dict[str, Any]) -> str:
    """Take the API token from the request payload, falling back to env var."""
    for key in ("api_key", "biohub_api_key", "token"):
        raw = payload.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    fallback = os.getenv("BIOHUB_API_KEY", "").strip()
    if fallback:
        return fallback
    raise RuntimeError(
        "Biohub API key required. Provide 'api_key' in the request payload "
        "or set BIOHUB_API_KEY on the worker service."
    )


def _model_name(payload: dict[str, Any] | None = None) -> str:
    if payload:
        raw = str(payload.get("model_name") or payload.get("model") or "").strip()
        if raw:
            return raw
    return os.getenv("ESMFOLD2_MODEL", "esmfold2-fast-2026-05").strip() or "esmfold2-fast-2026-05"


def _api_url() -> str:
    return os.getenv("BIOHUB_API_URL", "https://biohub.ai").strip() or "https://biohub.ai"


def _client(token: str, model: str) -> Any:
    cache_key = (token, model)
    cached = _CLIENT_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        from esm.sdk.forge import SequenceStructureForgeInferenceClient
    except ImportError as exc:
        raise RuntimeError(
            "esm SDK not installed. Install with: "
            "pip install 'esm@git+https://github.com/Biohub/esm.git@c94ed8d'"
        ) from exc
    client = SequenceStructureForgeInferenceClient(model=model, url=_api_url(), token=token)
    _CLIENT_CACHE[cache_key] = client
    if len(_CLIENT_CACHE) > 32:
        _CLIENT_CACHE.pop(next(iter(_CLIENT_CACHE)))
    return client


def _clean_sequence(raw: str) -> str:
    if raw.startswith(">"):
        parts: list[str] = []
        in_record = False
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith(">"):
                if in_record and parts:
                    break
                in_record = True
                continue
            if in_record:
                parts.append(stripped)
        raw = "".join(parts)
    return "".join(ch for ch in raw if ch.isalpha()).upper()


def _sequence_records(payload: dict[str, Any]) -> list[tuple[str, str]]:
    raw_records = payload.get("sequences")
    if isinstance(raw_records, list) and raw_records:
        out: list[tuple[str, str]] = []
        for idx, item in enumerate(raw_records):
            if not isinstance(item, dict):
                continue
            seq_id = str(item.get("id") or f"seq_{idx + 1}")
            seq = _clean_sequence(str(item.get("sequence") or ""))
            if seq:
                out.append((seq_id, seq))
        if out:
            return out
    raw = str(payload.get("sequence") or payload.get("query_sequence") or "").strip()
    if raw:
        return [("seq_1", _clean_sequence(raw))]
    raise ValueError("'sequence' or 'sequences' is required")


def _structure_to_pdb(structure: Any) -> str:
    """Extract a PDB string from whatever shape the Forge client returns."""
    if isinstance(structure, str):
        return structure
    for attr in ("pdb", "pdb_string", "to_pdb"):
        value = getattr(structure, attr, None)
        if callable(value):
            return str(value())
        if isinstance(value, str) and value.strip():
            return value
    if isinstance(structure, dict):
        for key in ("pdb", "pdb_string", "structure"):
            if isinstance(structure.get(key), str) and structure[key].strip():
                return structure[key]
    raise RuntimeError(f"Could not extract PDB from response: {type(structure).__name__}")


def _fold_one(client: Any, sequence: str) -> str:
    """Call whichever fold method the installed SDK exposes."""
    for method in ("fold", "predict", "run", "infer", "__call__"):
        fn = getattr(client, method, None)
        if not callable(fn):
            continue
        try:
            result = fn(sequence=sequence)
        except TypeError:
            try:
                result = fn(sequence)
            except Exception:
                continue
        except Exception:
            continue
        return _structure_to_pdb(result)
    # Fallback: build an ESMProtein and try .fold()
    try:
        from esm.sdk.api import ESMProtein  # type: ignore

        protein = ESMProtein(sequence=sequence)
        result = client.fold(protein)
        return _structure_to_pdb(result)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"No working fold() entry point on Forge client: {exc}") from exc


def _health_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "ready": True,
        "model": "esmfold2",
        "default_model_name": _model_name(),
        "api_url": _api_url(),
        "shared_token_configured": bool(os.getenv("BIOHUB_API_KEY", "").strip()),
        "accepts_per_request_token": True,
    }


def _run_fold(payload: dict[str, Any]) -> dict[str, Any]:
    token = _resolve_token(payload)
    model = _model_name(payload)
    client = _client(token, model)
    records = _sequence_records(payload)
    structures = []
    for seq_id, seq in records:
        pdb_text = _fold_one(client, seq)
        structures.append({"id": seq_id, "sequence": seq, "pdb": pdb_text})
    output: dict[str, Any] = {
        "model": "esmfold2",
        "model_name": model,
        "structures": structures,
    }
    if len(structures) == 1:
        output["sequence"] = structures[0]["sequence"]
        output["pdb"] = structures[0]["pdb"]
    return output


class ESMFold2HTTPWorkerHandler(BaseHTTPRequestHandler):
    server_version = "ESMFold2HTTPWorker/1.0"

    def do_GET(self) -> None:
        if self.path.rstrip("/") != "/healthz":
            _json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return
        _json_response(self, HTTPStatus.OK, _health_payload())

    def do_POST(self) -> None:
        if self.path.rstrip("/") not in {"/run", "/cancel"}:
            _json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            if not isinstance(request, dict):
                raise ValueError("request JSON must be an object")
            if self.path.rstrip("/") == "/cancel":
                job_id = job_id_from_request(request)
                _json_response(self, HTTPStatus.OK, JOBS.cancel(job_id))
                return
            payload = request.get("input", request)
            if not isinstance(payload, dict):
                raise ValueError("input must be an object")
            job_id = job_id_from_request(request)
            output = _run_fold(payload)
            _json_response(self, HTTPStatus.OK, {"id": job_id, "status": "COMPLETED", "output": output})
        except Exception as exc:
            _json_response(
                self,
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "ok": False,
                    "status": "FAILED",
                    "error": str(exc),
                    "traceback": traceback.format_exc(limit=8),
                },
            )

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("ESMFOLD2_WORKER_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("ESMFOLD2_WORKER_PORT", "18108")))
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), ESMFold2HTTPWorkerHandler)
    print(f"listening: http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
