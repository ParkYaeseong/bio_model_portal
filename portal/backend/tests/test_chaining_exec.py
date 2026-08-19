import uuid
from pathlib import Path

import pytest

from app import chaining, models
from app import chaining_exec
from app.database import SessionLocal


def _user(db):
    u = models.User(username=f"t_{uuid.uuid4().hex[:8]}", password_hash="x")
    db.add(u); db.commit(); db.refresh(u)
    return u


def _finished_job(db, user, pipeline, artifacts):
    j = models.Job(user_id=user.id, title="src", pipeline=pipeline, status="completed")
    db.add(j); db.commit(); db.refresh(j)
    for name, path, kind in artifacts:
        db.add(models.Artifact(job_id=j.id, file_name=name, file_path=str(path), kind=kind))
    db.commit(); db.refresh(j)
    return j


def _capture(monkeypatch, captured):
    def fake_create(db_, *, user_id, title, pipeline, params, input_files=None, sequence=None):
        captured["input_files"] = [Path(p).name for p in (input_files or [])]
        captured["sequence"] = sequence
        j = models.Job(user_id=user_id, title=title, pipeline=pipeline, status="submitted")
        db_.add(j); db_.commit(); db_.refresh(j)
        return j
    monkeypatch.setattr(chaining_exec.job_bridge, "create_step_job", fake_create)


def test_submit_chained_plain(monkeypatch):
    with SessionLocal() as db:
        u = _user(db)
        captured = {}; _capture(monkeypatch, captured)
        job = chaining_exec.submit_chained(db, u, pipeline="esmfold", params={}, sequence="ACDEF")
        assert job.status == "submitted"
        assert captured["sequence"] == "ACDEF"


def test_submit_chained_files_from_source(monkeypatch, tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        pdb = tmp_path / "backbone.pdb"; pdb.write_text("ATOM\n")
        src = _finished_job(db, u, "rfdiffusion", [("backbone.pdb", pdb, "structure")])
        captured = {}; _capture(monkeypatch, captured)
        # DiffDock always needs a ligand; the chained structure is the receptor.
        chaining_exec.submit_chained(db, u, pipeline="diffdock",
                                     params={"ligand_smiles": "CCO"}, from_job_id=src.id)
        assert "backbone.pdb" in captured["input_files"]


def test_submit_chained_sequence_from_source(monkeypatch, tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        fa = tmp_path / "d.fasta"; fa.write_text(">d\nACDEFGHIKL\n")
        src = _finished_job(db, u, "proteinmpnn", [("d.fasta", fa, "generic")])
        captured = {}; _capture(monkeypatch, captured)
        chaining_exec.submit_chained(db, u, pipeline="colabfold", params={}, from_job_id=src.id)
        assert captured["sequence"] == "ACDEFGHIKL"


def test_submit_chained_unknown_pipeline_raises():
    with SessionLocal() as db:
        u = _user(db)
        with pytest.raises(ValueError, match="unknown pipeline"):
            chaining_exec.submit_chained(db, u, pipeline="nope", params={})


def test_submit_chained_unfinished_source_raises(monkeypatch):
    with SessionLocal() as db:
        u = _user(db)
        j = models.Job(user_id=u.id, title="r", pipeline="rfdiffusion", status="running")
        db.add(j); db.commit(); db.refresh(j)
        with pytest.raises(ValueError, match="not finished"):
            chaining_exec.submit_chained(db, u, pipeline="diffdock", params={}, from_job_id=j.id)


def test_submit_chained_rfdiffusion_without_spec_raises():
    with SessionLocal() as db:
        u = _user(db)
        with pytest.raises(ValueError, match="RFdiffusion needs a design spec"):
            chaining_exec.submit_chained(db, u, pipeline="rfdiffusion", params={}, sequence="ACDEF")


def test_submit_chained_rfdiffusion_with_length_ok(monkeypatch):
    with SessionLocal() as db:
        u = _user(db)
        captured = {}; _capture(monkeypatch, captured)
        job = chaining_exec.submit_chained(db, u, pipeline="rfdiffusion", params={"length": "100"})
        assert job.status == "submitted"


def test_submit_chained_rfdiffusion_with_uploaded_pdb_ok(monkeypatch):
    with SessionLocal() as db:
        u = _user(db)
        captured = {}; _capture(monkeypatch, captured)
        import base64
        pdb_b64 = base64.b64encode(b"ATOM      1  N   ALA A   1\n").decode()
        job = chaining_exec.submit_chained(db, u, pipeline="rfdiffusion", params={},
                                           files=[{"name": "target.pdb", "base64": pdb_b64}])
        assert job.status == "submitted"
