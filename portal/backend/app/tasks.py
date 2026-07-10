from __future__ import annotations

import base64
import io
import tarfile
import zipfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import chaining_exec, models
from .config import get_settings
from .database import SessionLocal
from .phastest import convert_to_cgview, render_html_report
from .runpod import RunpodClient
from .runpod_status import extract_status
from .storage import remove_tree, results_dir

settings = get_settings()
ACTIVE_JOB_STATUSES = {
    "pending",
    "submitted",
    "running",
    "queued",
    "in_queue",
    "in_progress",
    "processing",
}
BASE64_ARCHIVE_KEYS: dict[str, tuple[str, str | None]] = {
    "result_zip_b64": (".zip", None),
    "result_tar_b64": (".tar", None),
    "result_tgz_b64": (".tar.gz", None),
    "result_tar_gz_b64": (".tar.gz", None),
    "out_dir_zip_b64": (".zip", "out_dir_zip_name"),
}


class JobMonitor:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="job-monitor", daemon=True)
        self.client: RunpodClient | None = None

    def start(self) -> None:
        if not self.thread.is_alive():
            self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.thread.join(timeout=5)

    def _run(self) -> None:
        try:
            self.client = RunpodClient()
        except RuntimeError as exc:
            print(f"[monitor] RunPod client disabled: {exc}")
            return
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as exc:  # noqa: BLE001
                print(f"[monitor] error: {exc}")
            finally:
                self._stop.wait(settings.poll_interval_seconds)

    def _poll_once(self) -> None:
        with SessionLocal() as db:
            jobs: Iterable[models.Job] = db.execute(
                select(models.Job).where(models.Job.status.in_(ACTIVE_JOB_STATUSES))
            ).scalars()
            for job in jobs:
                self._update_job(db, job)
            self._cleanup_expired(db)
            db.commit()

    def _update_job(self, db: Session, job: models.Job) -> None:
        if not self.client or not job.endpoint_id or not job.runpod_job_id:
            return
        try:
            response = self.client.status(job.endpoint_id, job.runpod_job_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                job.status = "failed"
                job.error_message = "RunPod job not found (worker purged)."
            else:
                job.error_message = f"RunPod status error: {exc.response.text[:200]}"
            return
        status_text, status_detail = extract_status(response)
        status_upper = (status_text or "").upper()
        if status_text:
            job.status = status_text.lower()
        output = response.get("output") or {}
        if isinstance(output, list):
            output = output[0] if output else {}
        elif not isinstance(output, dict):
            output = {}
        if status_upper == "COMPLETED" and output:
            job.error_message = None
            if (output_status := (output.get("status") or "").lower()) in {"error", "failed"}:
                job.status = "failed"
                job.error_message = output.get("message") or output.get("details") or "RunPod worker reported an error."
                self._advance_pending_chains(db, job, ok=False)
                return
            self._persist_output(db, job, output)
            self._advance_pending_chains(db, job, ok=True)
        elif status_upper in {"FAILED", "TIMED_OUT", "CANCELLED", "COMPLETED_WITH_ERRORS"}:
            job.error_message = response.get("error") or response.get("message") or status_detail
            self._advance_pending_chains(db, job, ok=False)

    def _advance_pending_chains(self, db: Session, job: models.Job, ok: bool) -> None:
        chains = db.query(models.PendingChain).filter_by(
            source_job_id=job.id, status="pending").all()
        for chain in chains:
            if not ok:
                chain.status = "cancelled"
                chain.error = f"source job {job.id} did not complete"
                continue
            chain.status = "processing"  # guard against a re-poll double-firing
            owner = db.get(models.User, chain.user_id)
            steps = list(chain.steps or [])
            step, rest = steps[0], steps[1:]
            try:
                new_job = chaining_exec.submit_chained(
                    db, owner,
                    pipeline=step["pipeline"],
                    params=step.get("parameters") or {},
                    from_job_id=job.id,
                    source_artifact_ids=step.get("source_artifact_ids"),
                )
            except Exception as exc:  # noqa: BLE001 (ChainError / ValueError)
                chain.status = "failed"
                chain.error = str(exc)
                continue
            if rest:
                chain.source_job_id = new_job.id
                chain.steps = rest
                chain.status = "pending"
            else:
                chain.status = "done"

    def _persist_output(self, db: Session, job: models.Job, output: dict) -> None:
        target_dir = results_dir(job.user_id, job.id)
        job.result_dir = str(target_dir)
        archives = list(output.get("archives") or [])
        if not archives:
            archive_b64 = output.get("archive_base64")
            if archive_b64:
                archives = [{"name": f"{job.id}.tar.gz", "base64": archive_b64}]
        for key, (extension, name_field) in BASE64_ARCHIVE_KEYS.items():
            base64_blob = output.get(key)
            if not base64_blob:
                continue
            file_name = output.get(name_field) if name_field else None
            if not file_name:
                base_name = output.get("job_name") or job.title or job.id
                file_name = f"{base_name}{extension}"
            archives.append({"name": file_name, "base64": base64_blob})
        for item in archives:
            base64_data = item.get("base64")
            if not base64_data:
                continue
            file_name = item.get("name") or f"{job.id}.tar.gz"
            raw = base64.b64decode(base64_data)
            archive_path = target_dir / file_name
            archive_path.write_bytes(raw)
            job.result_archive = str(archive_path)
            extracted = False
            try:
                with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
                    tar.extractall(target_dir)
                    extracted = True
            except tarfile.ReadError:
                extracted = False
            if not extracted:
                try:
                    with zipfile.ZipFile(io.BytesIO(raw)) as zip_ref:
                        zip_ref.extractall(target_dir)
                        extracted = True
                except zipfile.BadZipFile:
                    pass
            lower_name = file_name.lower()
            if lower_name.endswith(".zip"):
                mime_type = "application/zip"
            elif lower_name.endswith(".tar"):
                mime_type = "application/x-tar"
            else:
                mime_type = "application/gzip"
            artifact = models.Artifact(
                job_id=job.id,
                file_name=file_name,
                file_path=str(archive_path),
                kind="archive",
                mime_type=mime_type,
                size_bytes=len(raw),
            )
            db.add(artifact)
        self._persist_streams(db, job, target_dir, output)
        self._generate_phastest_assets(db, job, target_dir, output)
        self._index_results(db, job, target_dir)

    def _persist_streams(self, db: Session, job: models.Job, directory: Path, output: dict) -> None:
        for stream in ("stdout", "stderr"):
            content = output.get(stream)
            if not content:
                continue
            stream_path = directory / f"{stream}.log"
            stream_path.write_text(content)
            artifact = models.Artifact(
                job_id=job.id,
                file_name=stream_path.name,
                file_path=str(stream_path),
                kind="log",
                mime_type="text/plain",
                size_bytes=stream_path.stat().st_size,
            )
            db.add(artifact)

    def _index_results(self, db: Session, job: models.Job, directory: Path) -> None:
        if not directory.exists():
            return
        existing_paths = {
            artifact.file_path
            for artifact in db.query(models.Artifact).filter(models.Artifact.job_id == job.id).all()
        }
        for file in directory.rglob("*"):
            if not file.is_file():
                continue
            if str(file) in existing_paths:
                continue
            suffix = file.suffix.lower()
            kind = "generic"
            mime = "application/octet-stream"
            if suffix in {".pdb", ".cif"}:
                kind = "structure"
                mime = "chemical/x-pdb" if suffix == ".pdb" else "chemical/x-cif"
            elif suffix in {".json", ".csv"}:
                kind = "table"
                mime = "application/json" if suffix == ".json" else "text/csv"
            elif suffix in {".html"}:
                kind = "html"
                mime = "text/html"
            artifact = models.Artifact(
                job_id=job.id,
                file_name=file.name,
                file_path=str(file),
                kind=kind,
                mime_type=mime,
                size_bytes=file.stat().st_size,
            )
            db.add(artifact)

    def _cleanup_expired(self, db: Session) -> None:
        now = datetime.utcnow()
        expired_jobs = db.scalars(select(models.Job).where(models.Job.expires_at < now)).all()
        for job in expired_jobs:
            if job.result_dir:
                remove_tree(Path(job.result_dir))
            uploads_folder = Path(job.input_archive_path).parent if job.input_archive_path else None
            if uploads_folder and uploads_folder.exists():
                remove_tree(uploads_folder)
            db.delete(job)

    def _generate_phastest_assets(self, db: Session, job: models.Job, directory: Path, output: dict) -> None:
        if job.pipeline != "phastest":
            return
        sample_dir = self._resolve_phastest_sample_dir(directory, output.get("job_name"))
        if not sample_dir:
            return
        sample_label = (job.parameters or {}).get("sample_name") or output.get("job_name") or job.title
        cgview_path = convert_to_cgview(sample_dir, sample_label)
        if cgview_path and cgview_path.exists():
            artifact = models.Artifact(
                job_id=job.id,
                file_name=cgview_path.name,
                file_path=str(cgview_path),
                kind="json",
                mime_type="application/json",
                size_bytes=cgview_path.stat().st_size,
            )
            db.add(artifact)
        html_path = render_html_report(sample_dir, sample_label)
        if html_path and html_path.exists():
            artifact = models.Artifact(
                job_id=job.id,
                file_name=html_path.name,
                file_path=str(html_path),
                kind="html",
                mime_type="text/html",
                size_bytes=html_path.stat().st_size,
            )
            db.add(artifact)

    def _resolve_phastest_sample_dir(self, directory: Path, job_name: str | None) -> Path | None:
        if job_name:
            candidate = directory / str(job_name)
            if candidate.exists():
                return candidate
        subdirs = sorted([child for child in directory.iterdir() if child.is_dir()])
        return subdirs[0] if subdirs else None


monitor = JobMonitor()
