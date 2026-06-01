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

    class Config:
        orm_mode = True
