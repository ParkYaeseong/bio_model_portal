import uuid

from app import models
from app.database import SessionLocal


def test_pending_chain_roundtrips_steps_json():
    with SessionLocal() as db:
        u = models.User(username=f"t_{uuid.uuid4().hex[:8]}", password_hash="x")
        db.add(u); db.commit(); db.refresh(u)
        pc = models.PendingChain(
            user_id=u.id, source_job_id="job-1",
            steps=[{"pipeline": "proteinmpnn", "parameters": {}}], status="pending")
        db.add(pc); db.commit(); db.refresh(pc)
        assert pc.id and pc.status == "pending"
        assert pc.steps[0]["pipeline"] == "proteinmpnn"
