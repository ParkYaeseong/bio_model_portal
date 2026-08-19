from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    username: str | None = None


class UserBase(BaseModel):
    username: str


class UserCreate(UserBase):
    password: str = Field(min_length=6, max_length=72)


class UserRead(UserBase):
    id: int
    created_at: datetime

    class Config:
        orm_mode = True


class ArtifactRead(BaseModel):
    id: str
    file_name: str
    kind: str
    mime_type: str | None
    size_bytes: int

    class Config:
        orm_mode = True


class JobBase(BaseModel):
    title: str
    pipeline: str
    notes: str | None = None
    preferred_download_dir: str | None = None
    parameters: dict = Field(default_factory=dict)


class JobCreate(JobBase):
    pass


class JobRead(JobBase):
    id: str
    status: str
    runpod_job_id: str | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    artifacts: list[ArtifactRead] = []
    # Rough progress/queue estimate, only set while a job is active. All values
    # are approximate — workers report no true percentage. See queue_estimate.py.
    queue_position: int | None = None
    eta_seconds: int | None = None
    avg_seconds: int | None = None
    elapsed_seconds: int | None = None
    # Set only when an admin is viewing another account's job (never for the
    # owner's own jobs, admin or not).
    owner: str | None = None

    class Config:
        orm_mode = True
