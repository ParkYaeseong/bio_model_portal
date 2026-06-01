from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional
from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .config import get_settings
from .database import Base

settings = get_settings()


def _expires_at() -> datetime:
    return datetime.utcnow() + timedelta(days=settings.retention_days)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    jobs: Mapped[list[Job]] = relationship("Job", back_populates="user", cascade="all, delete-orphan")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    pipeline: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    runpod_job_id: Mapped[Optional[str]] = mapped_column(String(80))
    endpoint_id: Mapped[Optional[str]] = mapped_column(String(80))
    parameters: Mapped[dict | None] = mapped_column(JSON, default=dict)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    input_archive_path: Mapped[Optional[str]] = mapped_column(String(255))
    result_dir: Mapped[Optional[str]] = mapped_column(String(255))
    result_archive: Mapped[Optional[str]] = mapped_column(String(255))
    preferred_download_dir: Mapped[Optional[str]] = mapped_column(String(255))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, default=_expires_at)

    user: Mapped[User] = relationship("User", back_populates="jobs")
    artifacts: Mapped[list[Artifact]] = relationship("Artifact", back_populates="job", cascade="all, delete-orphan")


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), default="generic")
    mime_type: Mapped[Optional[str]] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    job: Mapped[Job] = relationship("Job", back_populates="artifacts")

