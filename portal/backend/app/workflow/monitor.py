from __future__ import annotations

import threading

from ..config import get_settings
from ..database import SessionLocal
from .. import models
from . import orchestrator

settings = get_settings()


def tick_once() -> None:
    with SessionLocal() as db:
        runs = db.query(models.WorkflowRun).filter_by(status="running").all()
        for run in runs:
            try:
                orchestrator.advance(db, run)
            except Exception as exc:  # noqa: BLE001
                print(f"[wf-monitor] run {run.id} error: {exc}")


class WorkflowMonitor:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="workflow-monitor", daemon=True)

    def start(self) -> None:
        if not self.thread.is_alive():
            self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                tick_once()
            except Exception as exc:  # noqa: BLE001
                print(f"[wf-monitor] error: {exc}")
            finally:
                self._stop.wait(settings.poll_interval_seconds)


workflow_monitor = WorkflowMonitor()
