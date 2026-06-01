from __future__ import annotations

from dataclasses import dataclass
import os
import signal
import subprocess
import threading
from typing import Any, Callable


@dataclass
class _ManagedJob:
    id: str
    proc: subprocess.Popen[str] | None = None
    cancel_callback: Callable[[], None] | None = None


class JobManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, _ManagedJob] = {}

    def start_process(self, job_id: str, cmd: list[str], **kwargs: Any) -> subprocess.Popen[str]:
        kwargs.setdefault("start_new_session", True)
        proc = subprocess.Popen(cmd, **kwargs)
        with self._lock:
            self._jobs[str(job_id)] = _ManagedJob(id=str(job_id), proc=proc)
        return proc

    def register_callback(self, job_id: str, callback: Callable[[], None]) -> None:
        with self._lock:
            self._jobs[str(job_id)] = _ManagedJob(id=str(job_id), cancel_callback=callback)

    def finish(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(str(job_id), None)

    def run_command(
        self,
        job_id: str,
        cmd: list[str],
        *,
        timeout_s: int | float | None = None,
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        proc = self.start_process(
            job_id,
            cmd,
            stdout=kwargs.pop("stdout", subprocess.PIPE),
            stderr=kwargs.pop("stderr", subprocess.PIPE),
            text=kwargs.pop("text", True),
            **kwargs,
        )
        try:
            try:
                stdout, stderr = proc.communicate(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                self.cancel(job_id)
                stdout, stderr = proc.communicate()
                raise TimeoutError(
                    f"Command timed out after {timeout_s}s: {' '.join(cmd)}\n"
                    f"stdout:\n{stdout}\nstderr:\n{stderr}"
                )
            return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
        finally:
            self.finish(job_id)

    def cancel(self, job_id: str) -> dict[str, Any]:
        key = str(job_id)
        with self._lock:
            job = self._jobs.get(key)
        if job is None:
            return {"id": key, "cancelled": False, "status": "not_found"}

        if job.cancel_callback is not None:
            job.cancel_callback()
            self.finish(key)
            return {"id": key, "cancelled": True, "status": "cancel_requested"}

        proc = job.proc
        if proc is None:
            self.finish(key)
            return {"id": key, "cancelled": False, "status": "not_cancellable"}
        if proc.poll() is not None:
            self.finish(key)
            return {"id": key, "cancelled": False, "status": "already_finished", "returncode": proc.returncode}

        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            self.finish(key)
            return {"id": key, "cancelled": False, "status": "not_found"}
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=5)
        for stream in (proc.stdout, proc.stderr):
            if stream is not None and not stream.closed:
                stream.close()
        self.finish(key)
        return {"id": key, "cancelled": True, "status": "cancelled", "returncode": proc.returncode}


def terminate_current_process_later(delay_s: float = 0.2) -> None:
    def _terminate() -> None:
        os.kill(os.getpid(), signal.SIGTERM)

    timer = threading.Timer(delay_s, _terminate)
    timer.daemon = True
    timer.start()


def job_id_from_request(request: dict[str, Any]) -> str:
    raw = str(request.get("id") or "").strip()
    if raw:
        return raw
    import uuid

    return uuid.uuid4().hex
