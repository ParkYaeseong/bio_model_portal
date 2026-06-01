from __future__ import annotations

import base64
import tarfile
import shutil
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from fastapi import UploadFile

from .config import get_settings

settings = get_settings()


def storage_path(*segments: str) -> Path:
    path = settings.storage_root.joinpath(*segments)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def uploads_dir(user_id: int, job_id: str) -> Path:
    path = settings.storage_root / settings.uploads_dir / str(user_id) / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def results_dir(user_id: int, job_id: str) -> Path:
    path = settings.storage_root / settings.results_dir / str(user_id) / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _sanitize_relative_path(filename: str | None) -> Path:
    raw = (filename or "").replace("\\", "/").strip()
    raw = raw.lstrip("./")
    parts = [part for part in raw.split("/") if part and part != ".."]
    if not parts:
        parts = [f"upload-{uuid4().hex}"]
    return Path(*parts)


def save_uploads(user_id: int, job_id: str, files: Iterable[UploadFile]) -> tuple[list[Path], Path]:
    saved_paths: list[Path] = []
    target_dir = uploads_dir(user_id, job_id)
    for upload in files:
        relative_path = _sanitize_relative_path(upload.filename)
        target_path = target_dir / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        upload.file.seek(0)
        with target_path.open("wb") as buffer:
            shutil.copyfileobj(upload.file, buffer)
        upload.file.close()
        saved_paths.append(target_path)
    return saved_paths, target_dir


def build_archive(paths: list[Path], archive_path: Path, base_dir: Path | None = None) -> Path:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w:gz") as tar:
        for path in paths:
            arcname = path.name
            if base_dir is not None:
                try:
                    arcname = path.relative_to(base_dir)
                except ValueError:
                    arcname = path.name
            tar.add(path, arcname=str(arcname).replace("\\", "/"))
    return archive_path


def file_to_base64(path: Path) -> str:
    data = path.read_bytes()
    return base64.b64encode(data).decode("ascii")


def remove_tree(path: Path) -> None:
    if not path.exists():
        return
    if path.is_file():
        path.unlink()
        return
    for child in path.iterdir():
        remove_tree(child)
    path.rmdir()
