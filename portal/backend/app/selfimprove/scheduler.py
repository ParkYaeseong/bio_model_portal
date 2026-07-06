from __future__ import annotations

import threading

from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import SessionLocal
from . import analyze

settings = get_settings()


def run_cycle(db: Session) -> "object":
    """Compute one artifact; auto-activate it if configured. Returns the artifact."""
    art = analyze.compute_artifact(db)
    if settings.selfimprove_autoactivate:
        from .. import models
        for other in db.query(models.ImprovementArtifact).filter_by(status="active").all():
            if other.id != art.id:
                other.status = "rejected"
        art.status = "active"
        db.commit()
        db.refresh(art)
    return art


class SelfImproveScheduler:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="selfimprove-scheduler", daemon=True)

    def start(self) -> None:
        if not settings.selfimprove_enabled:
            return
        if not self.thread.is_alive():
            self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                with SessionLocal() as db:
                    run_cycle(db)
            except Exception as exc:  # noqa: BLE001
                print(f"[selfimprove] cycle error: {exc}")
            finally:
                self._stop.wait(settings.selfimprove_interval_s)


selfimprove_scheduler = SelfImproveScheduler()
