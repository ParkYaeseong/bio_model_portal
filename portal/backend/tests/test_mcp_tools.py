import uuid
from pathlib import Path

from app import models
from app.database import SessionLocal
from app.mcp import tools


def _user(db):
    u = models.User(username=f"t_{uuid.uuid4().hex[:8]}", password_hash="x")
    db.add(u); db.commit(); db.refresh(u)
    return u


def test_list_models_includes_known_pipelines():
    with SessionLocal() as db:
        u = _user(db)
        out = tools.list_models(db, u, {})
        keys = {m["key"] for m in out["models"]}
        assert {"proteinmpnn", "esmfold", "colabfold"}.issubset(keys)
        af = next(m for m in out["models"] if m["key"] == "alphafold")
        assert any(f["name"] == "model_preset" for f in af["input_fields"])


def test_run_model_unknown_pipeline_errors():
    with SessionLocal() as db:
        u = _user(db)
        r = tools.run_model(db, u, {"pipeline": "nope"})
        assert r["ok"] is False and "unknown" in r["error"].lower()


def test_run_model_creates_job(monkeypatch):
    with SessionLocal() as db:
        u = _user(db)

        def fake_create(db_, *, user_id, title, pipeline, params, input_files=None, sequence=None):
            j = models.Job(user_id=user_id, title=title, pipeline=pipeline, status="submitted")
            db_.add(j); db_.commit(); db_.refresh(j)
            return j

        monkeypatch.setattr(tools.job_bridge, "create_step_job", fake_create)
        r = tools.run_model(db, u, {"pipeline": "esmfold", "sequence": "ACDEFG"})
        assert r["ok"] is True and r["job_id"]
        assert tools.job_status(db, u, {"job_id": r["job_id"]})["status"] == "submitted"


def test_job_status_enforces_ownership():
    with SessionLocal() as db:
        u1, u2 = _user(db), _user(db)
        j = models.Job(user_id=u1.id, title="x", pipeline="esmfold", status="submitted")
        db.add(j); db.commit(); db.refresh(j)
        r = tools.job_status(db, u2, {"job_id": j.id})
        assert r["ok"] is False


def _finished_job(db, user, pipeline, artifacts):
    """artifacts: list of (file_name, file_path, kind)."""
    j = models.Job(user_id=user.id, title="src", pipeline=pipeline, status="completed")
    db.add(j); db.commit(); db.refresh(j)
    for name, path, kind in artifacts:
        db.add(models.Artifact(job_id=j.id, file_name=name, file_path=str(path), kind=kind))
    db.commit(); db.refresh(j)
    return j


def _capture_create(monkeypatch, captured):
    def fake_create(db_, *, user_id, title, pipeline, params, input_files=None, sequence=None):
        captured["input_files"] = [Path(p).name for p in (input_files or [])]
        captured["sequence"] = sequence
        j = models.Job(user_id=user_id, title=title, pipeline=pipeline, status="submitted")
        db_.add(j); db_.commit(); db_.refresh(j)
        return j
    monkeypatch.setattr(tools.job_bridge, "create_step_job", fake_create)


def test_run_model_chains_structure_files(monkeypatch, tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        pdb = tmp_path / "backbone.pdb"; pdb.write_text("ATOM  1  N\n")
        src = _finished_job(db, u, "rfdiffusion", [("backbone.pdb", pdb, "structure")])
        captured = {}; _capture_create(monkeypatch, captured)
        r = tools.run_model(db, u, {"pipeline": "diffdock", "from_job_id": src.id})
        assert r["ok"] is True
        assert "backbone.pdb" in captured["input_files"]


def test_run_model_chains_sequence(monkeypatch, tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        fa = tmp_path / "designs.fasta"; fa.write_text(">d1\nACDEFGHIKL\n")
        src = _finished_job(db, u, "proteinmpnn", [("designs.fasta", fa, "generic")])
        captured = {}; _capture_create(monkeypatch, captured)
        r = tools.run_model(db, u, {"pipeline": "colabfold", "from_job_id": src.id})
        assert r["ok"] is True
        assert captured["sequence"] == "ACDEFGHIKL"


def test_run_model_chain_source_artifact_ids_override(monkeypatch, tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        pdb = tmp_path / "pick.pdb"; pdb.write_text("ATOM\n")
        other = tmp_path / "skip.pdb"; other.write_text("ATOM\n")
        src = _finished_job(db, u, "rfdiffusion",
                            [("pick.pdb", pdb, "structure"), ("skip.pdb", other, "structure")])
        picked = next(a.id for a in src.artifacts if a.file_name == "pick.pdb")
        captured = {}; _capture_create(monkeypatch, captured)
        r = tools.run_model(db, u, {"pipeline": "diffdock", "from_job_id": src.id,
                                    "source_artifact_ids": [picked]})
        assert r["ok"] is True
        assert captured["input_files"] == ["pick.pdb"]


def test_run_model_chain_rejects_other_users_job(monkeypatch, tmp_path):
    with SessionLocal() as db:
        u1, u2 = _user(db), _user(db)
        pdb = tmp_path / "b.pdb"; pdb.write_text("ATOM\n")
        src = _finished_job(db, u1, "rfdiffusion", [("b.pdb", pdb, "structure")])
        r = tools.run_model(db, u2, {"pipeline": "diffdock", "from_job_id": src.id})
        assert r["ok"] is False and "not found" in r["error"].lower()


def test_run_model_chain_rejects_unfinished_job(monkeypatch, tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        j = models.Job(user_id=u.id, title="run", pipeline="rfdiffusion", status="running")
        db.add(j); db.commit(); db.refresh(j)
        r = tools.run_model(db, u, {"pipeline": "diffdock", "from_job_id": j.id})
        assert r["ok"] is False and "not finished" in r["error"].lower()


def test_run_model_chain_incompatible_pair(monkeypatch, tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        pdb = tmp_path / "b.pdb"; pdb.write_text("ATOM\n")
        src = _finished_job(db, u, "rfdiffusion", [("b.pdb", pdb, "structure")])
        r = tools.run_model(db, u, {"pipeline": "mmseqs", "from_job_id": src.id})
        assert r["ok"] is False and "cannot feed" in r["error"].lower()


def test_run_model_chain_no_structure_artifacts(monkeypatch, tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        log = tmp_path / "stdout.log"; log.write_text("done\n")
        src = _finished_job(db, u, "rfdiffusion", [("stdout.log", log, "log")])
        r = tools.run_model(db, u, {"pipeline": "diffdock", "from_job_id": src.id})
        assert r["ok"] is False and "no structure artifacts" in r["error"].lower()


def test_run_chain_submits_first_and_queues_rest(monkeypatch):
    with SessionLocal() as db:
        u = _user(db)
        captured = {}; _capture_create(monkeypatch, captured)
        r = tools.run_chain(db, u, {
            "steps": [{"pipeline": "rfdiffusion", "parameters": {"length": "100"}},
                      {"pipeline": "proteinmpnn", "parameters": {}}],
            "sequence": "ACDEF",
        })
        assert r["ok"] is True and r["first_job_id"]
        assert r["queued"] == ["proteinmpnn"]
        chains = db.query(models.PendingChain).filter_by(source_job_id=r["first_job_id"]).all()
        assert len(chains) == 1
        assert chains[0].steps == [{"pipeline": "proteinmpnn", "parameters": {}}]


def test_run_chain_single_step_creates_no_pending(monkeypatch):
    with SessionLocal() as db:
        u = _user(db)
        captured = {}; _capture_create(monkeypatch, captured)
        r = tools.run_chain(db, u, {"steps": [{"pipeline": "esmfold"}], "sequence": "ACDEF"})
        assert r["ok"] is True and r["queued"] == []
        assert db.query(models.PendingChain).filter_by(source_job_id=r["first_job_id"]).count() == 0


def test_run_chain_unknown_pipeline_errors(monkeypatch):
    with SessionLocal() as db:
        u = _user(db)
        r = tools.run_chain(db, u, {"steps": [{"pipeline": "nope"}]})
        assert r["ok"] is False and "unknown pipeline" in r["error"].lower()
