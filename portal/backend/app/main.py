from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .database import Base, engine
from .routers import assistant, auth, jobs, pipelines, rfdiffusion, users
from .tasks import monitor
from .workflow.monitor import workflow_monitor

settings = get_settings()
Base.metadata.create_all(bind=engine)

app = FastAPI(title=settings.app_name)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ALLOW_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(pipelines.router)
app.include_router(jobs.router)
app.include_router(assistant.router)
app.include_router(rfdiffusion.router)


@app.on_event("startup")
def start_monitor() -> None:
    monitor.start()
    workflow_monitor.start()


@app.on_event("shutdown")
def stop_monitor() -> None:
    monitor.stop()
    workflow_monitor.stop()


@app.get("/health")
def health():
    return {"ok": True}
