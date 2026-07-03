from pathlib import Path

from app import models
from app.database import Base, engine, SessionLocal
from app.workflow import job_bridge


class _FakeClient:
    def submit(self, endpoint_id, payload):
        _FakeClient.last = (endpoint_id, payload)
        return "rp-123"


def test_create_step_job_submits_and_persists(monkeypatch, tmp_path):
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(job_bridge, "RunpodClient", lambda: _FakeClient())
    monkeypatch.setattr(job_bridge, "pipeline_endpoint", lambda key: "endpoint-xyz")
    with SessionLocal() as db:
        user = models.User(username="jb_tester", password_hash="x")
        db.add(user)
        db.commit()
        pdb = tmp_path / "bb.pdb"
        pdb.write_text("ATOM      1  N   ALA A   1       0.0   0.0   0.0\n")
        job = job_bridge.create_step_job(
            db, user_id=user.id, title="mpnn step",
            pipeline="proteinmpnn", params={"num_seq_per_target": 4},
            input_files=[pdb], sequence=None,
        )
        assert job.pipeline == "proteinmpnn"
        assert job.endpoint_id == "endpoint-xyz"
        assert job.runpod_job_id == "rp-123"
        assert job.status in {"submitted", "in_queue", "pending"}
