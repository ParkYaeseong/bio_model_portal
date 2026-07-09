from __future__ import annotations

import json
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

# Jobs are tracked in-memory; persist them so a gateway restart resumes in-flight
# RunPod jobs instead of orphaning them (a lost job surfaces to the portal as a
# status 404 => permanent "failed"). RunPod passthrough jobs (e.g. AlphaFold) can
# run for hours, so surviving a restart matters.
STATE_PATH = Path(
    os.getenv("GATEWAY_STATE_PATH", Path(__file__).resolve().parent / "jobs_state.json")
)


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


def _save_jobs() -> None:
    """Persist JOBS to disk atomically. Caller must hold JOBS_LOCK.

    Persistence must never crash a running job, so failures are logged, not
    raised."""
    try:
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(JOBS))
        tmp.replace(STATE_PATH)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("could not persist job state to %s: %s", STATE_PATH, exc)


def _mark_failed(job_id: str, error: str, *, raw: dict | None = None) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is not None:
            job["status"] = "FAILED"
            job["error"] = error
            if raw is not None:
                job["output"] = {"raw": raw}
            job["completed_at"] = time.time()
        _save_jobs()


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
            _save_jobs()
    if dead:
        LOG.info("gc removed %d expired job(s)", len(dead))


def _runpod_submit(runpod_id: str, adapted: dict) -> str:
    """POST to RunPod serverless /run and return the RunPod job id."""
    if not RUNPOD_API_KEY:
        raise RuntimeError("RUNPOD_API_KEY is not configured for RunPod passthrough")
    headers = {"Authorization": f"Bearer {RUNPOD_API_KEY}"}
    with httpx.Client(timeout=60) as client:
        r = client.post(
            f"{RUNPOD_API_BASE}/{runpod_id}/run", headers=headers, json={"input": adapted}
        )
        r.raise_for_status()
        sub = r.json() if r.content else {}
    rp_job = sub.get("id")
    if not rp_job:
        raise RuntimeError(f"runpod did not return a job id: {sub}")
    return str(rp_job)


def _runpod_poll(runpod_id: str, rp_job: str) -> dict:
    """Poll a known RunPod job to completion. Returns a worker-style dict so the
    finalize/packaging path is identical to the local-worker path. Because the
    RunPod job id is known, this resumes cleanly after a gateway restart."""
    if not RUNPOD_API_KEY:
        raise RuntimeError("RUNPOD_API_KEY is not configured for RunPod passthrough")
    headers = {"Authorization": f"Bearer {RUNPOD_API_KEY}"}
    deadline = time.time() + RUNPOD_POLL_TIMEOUT_S
    sub: dict = {}
    rp_status = ""
    with httpx.Client(timeout=60) as client:
        while True:
            sr = client.get(
                f"{RUNPOD_API_BASE}/{runpod_id}/status/{rp_job}", headers=headers
            )
            sr.raise_for_status()
            sub = sr.json()
            rp_status = str(sub.get("status") or "").upper()
            if rp_status not in {"IN_QUEUE", "IN_PROGRESS", ""}:
                break
            if time.time() > deadline:
                raise RuntimeError(
                    f"runpod job {rp_job} timed out after {RUNPOD_POLL_TIMEOUT_S}s"
                )
            time.sleep(RUNPOD_POLL_INTERVAL_S)
    if rp_status == "COMPLETED":
        return {"status": "COMPLETED", "output": sub.get("output")}
    return {
        "status": "FAILED",
        "error": sub.get("error") or f"runpod status {rp_status or 'unknown'}",
        "output": sub.get("output"),
    }


def _finalize_from_worker_data(job_id: str, data: Any, packager_name: str) -> None:
    """Given a worker-style dict ({status, output, ...}), detect failure, package
    the output, and record the terminal job state. Shared by the initial run and
    the post-restart resume path."""
    output = data.get("output") if isinstance(data, dict) else None
    if not isinstance(output, dict):
        output = {"raw_response": data}

    worker_status = (data.get("status") if isinstance(data, dict) else "") or ""
    if worker_status.upper() in {"FAILED", "ERROR"} or (
        isinstance(data, dict) and data.get("ok") is False
    ):
        _mark_failed(
            job_id,
            output.get("message")
            or output.get("error")
            or (data.get("error") if isinstance(data, dict) else None)
            or "worker reported failure",
            raw=output,
        )
        return

    try:
        archive_b64 = package(packager_name, output)
    except Exception as exc:  # noqa: BLE001
        LOG.exception("job %s packager %s failed", job_id, packager_name)
        _mark_failed(job_id, f"packager {packager_name} failed: {exc}", raw=output)
        return

    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is not None:
            job["status"] = "COMPLETED"
            job["output"] = {
                "archives": [{"name": f"{job_id}.tar.gz", "base64": archive_b64}],
                "stdout": output.get("stdout_tail") or output.get("stdout") or "",
                "stderr": output.get("stderr_tail") or output.get("stderr") or "",
                "raw": output,
            }
            job["completed_at"] = time.time()
        _save_jobs()
    LOG.info("job %s completed", job_id)


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
            _save_jobs()
            return
        job["status"] = "IN_PROGRESS"
        job["started_at"] = time.time()
        worker_url = endpoint["worker_url"]
        runpod_id = endpoint.get("runpod_endpoint_id") or ""
        packager_name = endpoint["packager"]
        adapter_name = endpoint["adapter"]
        payload = job["input"]
        _save_jobs()

    try:
        adapted = adapt(adapter_name, payload) if adapter_name else payload
    except Exception as exc:  # noqa: BLE001
        LOG.exception("job %s adapter %s failed", job_id, adapter_name)
        _mark_failed(job_id, f"adapter {adapter_name} failed: {exc}")
        return

    backend_label = f"runpod:{runpod_id}" if runpod_id else worker_url
    LOG.info("job %s -> %s (adapter=%s)", job_id, backend_label, adapter_name or "none")
    try:
        if runpod_id:
            # Record the RunPod job id BEFORE polling so a restart mid-run can
            # reconnect to the same RunPod job instead of orphaning it.
            rp_job = _runpod_submit(runpod_id, adapted)
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if job is not None:
                    job["rp_job"] = rp_job
                    job["runpod_id"] = runpod_id
                _save_jobs()
            data = _runpod_poll(runpod_id, rp_job)
        else:
            with httpx.Client(timeout=None) as client:
                response = client.post(
                    f"{worker_url}/run",
                    json={"input": adapted, "id": job_id},
                )
            if response.status_code >= 400:
                _mark_failed(
                    job_id,
                    f"worker http {response.status_code}: {response.text[:1000]}",
                )
                return
            data = response.json()
    except Exception as exc:  # noqa: BLE001
        LOG.exception("job %s worker call failed", job_id)
        _mark_failed(job_id, f"{type(exc).__name__}: {exc}")
        return

    _finalize_from_worker_data(job_id, data, packager_name)


def _resume_runpod_job(job_id: str) -> None:
    """Re-poll a RunPod job whose polling thread died in a gateway restart."""
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        runpod_id = job.get("runpod_id") or ""
        rp_job = job.get("rp_job") or ""
        endpoint = ENDPOINTS.get(job["endpoint_id"])
        packager_name = endpoint["packager"] if endpoint else "generic"
    if not (runpod_id and rp_job):
        return
    LOG.info("resuming runpod job %s (rp=%s)", job_id, rp_job)
    try:
        data = _runpod_poll(runpod_id, rp_job)
    except Exception as exc:  # noqa: BLE001
        LOG.exception("resume of job %s failed", job_id)
        _mark_failed(job_id, f"{type(exc).__name__}: {exc}")
        return
    _finalize_from_worker_data(job_id, data, packager_name)


def _load_jobs() -> None:
    """Load persisted jobs at startup and resume in-flight RunPod jobs.

    A RunPod job with a stored id is re-polled; anything else still in flight
    (local synchronous worker, or a RunPod job that never got an id) cannot be
    reconnected, so it is failed with an actionable message rather than left to
    surface as an opaque status 404."""
    if not STATE_PATH.exists():
        return
    try:
        data = json.loads(STATE_PATH.read_text())
    except Exception as exc:  # noqa: BLE001
        LOG.warning("could not load job state from %s: %s", STATE_PATH, exc)
        return
    if not isinstance(data, dict):
        return
    with JOBS_LOCK:
        JOBS.update(data)
    resumed = 0
    orphaned = 0
    for jid, job in list(data.items()):
        if not isinstance(job, dict) or job.get("status") not in {"IN_QUEUE", "IN_PROGRESS"}:
            continue
        if job.get("rp_job") and job.get("runpod_id"):
            threading.Thread(target=_resume_runpod_job, args=(jid,), daemon=True).start()
            resumed += 1
        else:
            _mark_failed(
                jid,
                "gateway restarted while the job was running; please re-run",
            )
            orphaned += 1
    LOG.info(
        "startup: loaded %d job(s), resumed %d runpod job(s), failed %d unrecoverable",
        len(data),
        resumed,
        orphaned,
    )


app = FastAPI(title="Local RunPod-compatible Gateway")


@app.on_event("startup")
def _startup() -> None:
    _load_endpoints()
    _load_jobs()


@app.get("/health")
def health() -> dict:
    return {"ok": True, "endpoints": sorted(ENDPOINTS.keys())}


@app.post("/prep/contig-suggestions")
def contig_suggestions(body: dict = Body(...), authorization: str | None = Header(default=None)):
    _check_auth(authorization)
    import base64
    from prep import contig_suggest, structure
    pdb_text = None
    if isinstance(body.get("pdb_base64"), str) and body["pdb_base64"]:
        pdb_text = base64.b64decode(body["pdb_base64"]).decode("utf-8", errors="replace")
    else:
        pdb_text = structure.extract_pdb_text(body)
    fallback = {"chains": [], "options": [{"id": "custom", "label": "직접 입력", "contig": "", "recommended": True}], "processed_coords": True}
    if not pdb_text:
        return fallback
    try:
        return contig_suggest.suggest(pdb_text)
    except Exception as exc:  # noqa: BLE001
        return {**fallback, "error": str(exc)}


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
            "rp_job": None,
            "runpod_id": None,
            "submitted_at": time.time(),
            "started_at": None,
            "completed_at": None,
        }
        _save_jobs()
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
        worker_url = ENDPOINTS.get(endpoint_id, {}).get("worker_url") or ""
        runpod_id = job.get("runpod_id") or ""
        rp_job = job.get("rp_job") or ""
    # Best-effort stop the ACTUAL compute so cancel frees the GPU, not just the
    # local record. RunPod job id is now persisted (see _run_job), so a RunPod
    # passthrough job can be cancelled remotely; local workers get /cancel.
    if runpod_id and rp_job and RUNPOD_API_KEY:
        try:
            with httpx.Client(timeout=10) as client:
                client.post(
                    f"{RUNPOD_API_BASE}/{runpod_id}/cancel/{rp_job}",
                    headers={"Authorization": f"Bearer {RUNPOD_API_KEY}"},
                )
        except Exception as exc:  # noqa: BLE001
            LOG.warning("runpod cancel failed for %s (rp=%s): %s", job_id, rp_job, exc)
    elif worker_url:
        try:
            with httpx.Client(timeout=10) as client:
                client.post(f"{worker_url}/cancel", json={"id": job_id})
        except Exception as exc:  # noqa: BLE001
            LOG.warning("worker cancel call failed for %s: %s", job_id, exc)
    with JOBS_LOCK:
        if job_id in JOBS and JOBS[job_id]["status"] not in {"COMPLETED", "FAILED"}:
            JOBS[job_id]["status"] = "FAILED"
            JOBS[job_id]["error"] = "cancelled by user"
            JOBS[job_id]["completed_at"] = time.time()
        _save_jobs()
    return {"id": job_id, "status": "FAILED"}
