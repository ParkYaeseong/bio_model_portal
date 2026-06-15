from __future__ import annotations

import logging
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import yaml
from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from adapters import adapt  # noqa: E402
from packagers import package  # noqa: E402

LOG = logging.getLogger("gateway")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

CONFIG_PATH = Path(os.getenv("GATEWAY_ENDPOINTS", Path(__file__).parent / "endpoints.yaml"))
GATEWAY_TOKEN = os.getenv("GATEWAY_TOKEN", "").strip() or None
JOB_TTL_SECONDS = int(os.getenv("GATEWAY_JOB_TTL_SECONDS", "86400"))

# RunPod passthrough: endpoints declared with `runpod_endpoint_id` (instead of a
# local `worker_url`) are forwarded to RunPod serverless. RunPod's /status output
# is the same handler payload a local worker returns, so packaging is unchanged.
RUNPOD_API_KEY = os.getenv("RUNPOD_API_KEY", "").strip() or None
RUNPOD_API_BASE = os.getenv("RUNPOD_API_BASE", "https://api.runpod.ai/v2").rstrip("/")
RUNPOD_POLL_TIMEOUT_S = int(os.getenv("RUNPOD_POLL_TIMEOUT_S", "3600"))
RUNPOD_POLL_INTERVAL_S = float(os.getenv("RUNPOD_POLL_INTERVAL_S", "5"))

ENDPOINTS: dict[str, dict[str, str]] = {}
JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()


def _load_endpoints() -> None:
    raw = yaml.safe_load(CONFIG_PATH.read_text())
    entries = (raw or {}).get("endpoints") or {}
    if not isinstance(entries, dict) or not entries:
        raise RuntimeError(f"no endpoints in {CONFIG_PATH}")
    for key, value in entries.items():
        if not isinstance(value, dict):
            raise RuntimeError(f"endpoint {key!r} invalid")
        worker_url = value.get("worker_url")
        runpod_id = value.get("runpod_endpoint_id")
        if not worker_url and not runpod_id:
            raise RuntimeError(
                f"endpoint {key!r} needs worker_url or runpod_endpoint_id"
            )
        ENDPOINTS[str(key)] = {
            "worker_url": str(worker_url).rstrip("/") if worker_url else "",
            "runpod_endpoint_id": str(runpod_id) if runpod_id else "",
            "packager": str(value.get("packager") or "generic"),
            "adapter": str(value.get("adapter") or ""),
        }
    LOG.info("loaded %d endpoints from %s", len(ENDPOINTS), CONFIG_PATH)


def _check_auth(authorization: str | None) -> None:
    if not GATEWAY_TOKEN:
        return
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    if authorization.split(None, 1)[1].strip() != GATEWAY_TOKEN:
        raise HTTPException(401, "invalid token")


def _gc_jobs() -> None:
    cutoff = time.time() - JOB_TTL_SECONDS
    with JOBS_LOCK:
        dead = [
            jid
            for jid, job in JOBS.items()
            if job["status"] in {"COMPLETED", "FAILED"}
            and (job.get("completed_at") or job["submitted_at"]) < cutoff
        ]
        for jid in dead:
            JOBS.pop(jid, None)
    if dead:
        LOG.info("gc removed %d expired job(s)", len(dead))


def _call_runpod(runpod_id: str, adapted: dict, job_id: str) -> dict:
    """Submit to RunPod serverless and poll to completion.

    Returns a worker-style dict ({"status": ..., "output": ...}) so the rest of
    _run_job (failure detection + packaging) is identical to the local path.
    """
    if not RUNPOD_API_KEY:
        raise RuntimeError("RUNPOD_API_KEY is not configured for RunPod passthrough")
    headers = {"Authorization": f"Bearer {RUNPOD_API_KEY}"}
    with httpx.Client(timeout=60) as client:
        r = client.post(
            f"{RUNPOD_API_BASE}/{runpod_id}/run",
            headers=headers,
            json={"input": adapted},
        )
        r.raise_for_status()
        sub = r.json() if r.content else {}
        rp_job = sub.get("id")
        rp_status = str(sub.get("status") or "").upper()
        deadline = time.time() + RUNPOD_POLL_TIMEOUT_S
        while rp_job and rp_status in {"IN_QUEUE", "IN_PROGRESS", ""}:
            if time.time() > deadline:
                raise RuntimeError(
                    f"runpod job {rp_job} timed out after {RUNPOD_POLL_TIMEOUT_S}s"
                )
            time.sleep(RUNPOD_POLL_INTERVAL_S)
            sr = client.get(
                f"{RUNPOD_API_BASE}/{runpod_id}/status/{rp_job}", headers=headers
            )
            sr.raise_for_status()
            sub = sr.json()
            rp_status = str(sub.get("status") or "").upper()

    if rp_status == "COMPLETED":
        return {"status": "COMPLETED", "output": sub.get("output")}
    return {
        "status": "FAILED",
        "error": sub.get("error") or f"runpod status {rp_status or 'unknown'}",
        "output": sub.get("output"),
    }


def _run_job(job_id: str) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        endpoint = ENDPOINTS.get(job["endpoint_id"])
        if not endpoint:
            job["status"] = "FAILED"
            job["error"] = f"endpoint {job['endpoint_id']} disappeared"
            job["completed_at"] = time.time()
            return
        job["status"] = "IN_PROGRESS"
        job["started_at"] = time.time()
        worker_url = endpoint["worker_url"]
        runpod_id = endpoint.get("runpod_endpoint_id") or ""
        packager_name = endpoint["packager"]
        adapter_name = endpoint["adapter"]
        payload = job["input"]

    try:
        adapted = adapt(adapter_name, payload) if adapter_name else payload
    except Exception as exc:  # noqa: BLE001
        LOG.exception("job %s adapter %s failed", job_id, adapter_name)
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "FAILED"
            JOBS[job_id]["error"] = f"adapter {adapter_name} failed: {exc}"
            JOBS[job_id]["completed_at"] = time.time()
        return

    backend_label = f"runpod:{runpod_id}" if runpod_id else worker_url
    LOG.info("job %s -> %s (adapter=%s)", job_id, backend_label, adapter_name or "none")
    try:
        if runpod_id:
            data = _call_runpod(runpod_id, adapted, job_id)
        else:
            with httpx.Client(timeout=None) as client:
                response = client.post(
                    f"{worker_url}/run",
                    json={"input": adapted, "id": job_id},
                )
            if response.status_code >= 400:
                with JOBS_LOCK:
                    JOBS[job_id]["status"] = "FAILED"
                    JOBS[job_id]["error"] = (
                        f"worker http {response.status_code}: {response.text[:1000]}"
                    )
                    JOBS[job_id]["completed_at"] = time.time()
                return
            data = response.json()
    except Exception as exc:  # noqa: BLE001
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "FAILED"
            JOBS[job_id]["error"] = f"{type(exc).__name__}: {exc}"
            JOBS[job_id]["completed_at"] = time.time()
        LOG.exception("job %s worker call failed", job_id)
        return

    output = data.get("output") if isinstance(data, dict) else None
    if not isinstance(output, dict):
        output = {"raw_response": data}

    worker_status = (data.get("status") if isinstance(data, dict) else "") or ""
    if worker_status.upper() in {"FAILED", "ERROR"} or data.get("ok") is False:
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "FAILED"
            JOBS[job_id]["error"] = (
                output.get("message")
                or output.get("error")
                or data.get("error")
                or "worker reported failure"
            )
            JOBS[job_id]["output"] = {"raw": output}
            JOBS[job_id]["completed_at"] = time.time()
        return

    try:
        archive_b64 = package(packager_name, output)
    except Exception as exc:  # noqa: BLE001
        LOG.exception("job %s packager %s failed", job_id, packager_name)
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "FAILED"
            JOBS[job_id]["error"] = f"packager {packager_name} failed: {exc}"
            JOBS[job_id]["output"] = {"raw": output}
            JOBS[job_id]["completed_at"] = time.time()
        return

    with JOBS_LOCK:
        JOBS[job_id]["status"] = "COMPLETED"
        JOBS[job_id]["output"] = {
            "archives": [{"name": f"{job_id}.tar.gz", "base64": archive_b64}],
            "stdout": output.get("stdout_tail") or output.get("stdout") or "",
            "stderr": output.get("stderr_tail") or output.get("stderr") or "",
            "raw": output,
        }
        JOBS[job_id]["completed_at"] = time.time()
    LOG.info("job %s completed", job_id)


app = FastAPI(title="Local RunPod-compatible Gateway")


@app.on_event("startup")
def _startup() -> None:
    _load_endpoints()


@app.get("/health")
def health() -> dict:
    return {"ok": True, "endpoints": sorted(ENDPOINTS.keys())}


@app.get("/v2/{endpoint_id}/health")
def endpoint_health(endpoint_id: str, authorization: str | None = Header(default=None)):
    _check_auth(authorization)
    endpoint = ENDPOINTS.get(endpoint_id)
    if not endpoint:
        raise HTTPException(404, f"unknown endpoint {endpoint_id}")
    runpod_id = endpoint.get("runpod_endpoint_id") or ""
    if runpod_id:
        try:
            headers = (
                {"Authorization": f"Bearer {RUNPOD_API_KEY}"} if RUNPOD_API_KEY else {}
            )
            with httpx.Client(timeout=10) as client:
                r = client.get(
                    f"{RUNPOD_API_BASE}/{runpod_id}/health", headers=headers
                )
            return {
                "endpoint_id": endpoint_id,
                "backend": "runpod",
                "worker_status": r.status_code,
                "body": r.json() if r.content else {},
            }
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                {"endpoint_id": endpoint_id, "backend": "runpod", "worker_status": "unreachable", "error": str(exc)},
                status_code=502,
            )
    try:
        with httpx.Client(timeout=5) as client:
            r = client.get(f"{endpoint['worker_url']}/healthz")
        return {"endpoint_id": endpoint_id, "worker_status": r.status_code, "body": r.json()}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            {"endpoint_id": endpoint_id, "worker_status": "unreachable", "error": str(exc)},
            status_code=502,
        )


@app.post("/v2/{endpoint_id}/run")
def submit(
    endpoint_id: str,
    body: dict = Body(...),
    authorization: str | None = Header(default=None),
) -> dict:
    _check_auth(authorization)
    if endpoint_id not in ENDPOINTS:
        raise HTTPException(404, f"unknown endpoint {endpoint_id}")
    job_id = uuid.uuid4().hex
    payload = body.get("input") if isinstance(body, dict) else None
    if not isinstance(payload, dict):
        raise HTTPException(400, "request must be {'input': {...}}")
    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "endpoint_id": endpoint_id,
            "status": "IN_QUEUE",
            "input": payload,
            "output": None,
            "error": None,
            "submitted_at": time.time(),
            "started_at": None,
            "completed_at": None,
        }
    threading.Thread(target=_run_job, args=(job_id,), daemon=True).start()
    _gc_jobs()
    return {"id": job_id, "status": "IN_QUEUE"}


@app.get("/v2/{endpoint_id}/status/{job_id}")
def status(
    endpoint_id: str,
    job_id: str,
    authorization: str | None = Header(default=None),
) -> dict:
    _check_auth(authorization)
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(404, f"unknown job {job_id}")
        if job["endpoint_id"] != endpoint_id:
            raise HTTPException(404, "job/endpoint mismatch")
        resp: dict[str, Any] = {"id": job_id, "status": job["status"]}
        if job["output"] is not None:
            resp["output"] = job["output"]
        if job["error"]:
            resp["error"] = job["error"]
        return resp


@app.post("/v2/{endpoint_id}/cancel/{job_id}")
def cancel(
    endpoint_id: str,
    job_id: str,
    authorization: str | None = Header(default=None),
) -> dict:
    _check_auth(authorization)
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job or job["endpoint_id"] != endpoint_id:
            raise HTTPException(404, f"unknown job {job_id}")
        if job["status"] in {"COMPLETED", "FAILED"}:
            return {"id": job_id, "status": job["status"]}
        worker_url = ENDPOINTS[endpoint_id]["worker_url"]
    # RunPod jobs are polled in a background thread; we don't track the remote
    # RunPod job id here, so cancel is best-effort local-only for those.
    if worker_url:
        try:
            with httpx.Client(timeout=10) as client:
                client.post(f"{worker_url}/cancel", json={"id": job_id})
        except Exception as exc:  # noqa: BLE001
            LOG.warning("worker cancel call failed for %s: %s", job_id, exc)
    with JOBS_LOCK:
        if JOBS[job_id]["status"] not in {"COMPLETED", "FAILED"}:
            JOBS[job_id]["status"] = "FAILED"
            JOBS[job_id]["error"] = "cancelled by user"
            JOBS[job_id]["completed_at"] = time.time()
    return {"id": job_id, "status": "FAILED"}
