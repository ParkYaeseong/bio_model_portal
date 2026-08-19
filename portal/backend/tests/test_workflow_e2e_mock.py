"""Full RAPID DAG end-to-end with the GPU workers mocked.

Every gateway step's job is faked as an already-completed Job whose result_dir
holds a realistic small fixture (a3m / fasta / pdb). Driving the run through the
WorkflowMonitor tick loop must reach `completed` with scored candidates.
"""
import uuid

from app import models
from app.database import SessionLocal
from app.workflow import monitor, orchestrator

_PDB = (
    "ATOM      1 CA   ALA A   1       0.000   0.000   0.000  1.00 80.00           C\n"
    "ATOM      2 CA   GLY A   2       0.000   0.000   0.000  1.00 90.00           C\n"
)


def _fake_create_step_job(fixtures_root):
    def _create(db, *, user_id, title, pipeline, params, input_files=None, sequence=None):
        d = fixtures_root / f"{pipeline}_{uuid.uuid4().hex}"
        d.mkdir(parents=True)
        if pipeline == "mmseqs":
            (d / "out.a3m").write_text(">q\nACDEFG\n>h1\nACDEFG\n")
        elif pipeline == "proteinmpnn":
            (d / "designs.fa").write_text(">d1\nMKTAYIAKQR\n>d2\nGGGSGGGSAA\n")
        elif pipeline == "esmfold":
            (d / "ranked_0.pdb").write_text(_PDB)
        job = models.Job(user_id=user_id, title=title, pipeline=pipeline, status="completed")
        job.result_dir = str(d)
        db.add(job)
        db.commit()
        db.refresh(job)
        return job

    return _create


def test_rapid_run_reaches_completed_with_candidates(monkeypatch, tmp_path):
    monkeypatch.setattr(
        orchestrator.job_bridge, "create_step_job", _fake_create_step_job(tmp_path)
    )
    with SessionLocal() as db:
        user = models.User(username=f"e2e_{uuid.uuid4().hex[:8]}", password_hash="x")
        db.add(user)
        db.commit()
        wf = models.Workflow(name="RAPID", template_key="rapid_v1", owner_id=user.id, dag={})
        db.add(wf)
        db.commit()
        pdb = tmp_path / "backbone.pdb"
        pdb.write_text(_PDB)
        run = models.WorkflowRun(
            workflow_id=wf.id, owner_id=user.id, status="queued",
            input_summary={"sequence": "ACDEFGHIKL", "backbone_path": str(pdb)},
        )
        db.add(run)
        db.commit()

        orchestrator.start_run(db, run)
        for _ in range(40):
            if run.status in {"completed", "failed"}:
                break
            monitor.tick_once()
            db.refresh(run)

        assert run.status == "completed", f"run failed: {run.error_message}"
        steps = db.query(models.WorkflowRunStep).filter_by(run_id=run.id).all()
        assert len(steps) == 7
        assert all(s.status == "completed" for s in steps)
        candidates = (run.output_summary or {}).get("candidates", [])
        assert candidates, "no candidates in output_summary"
        assert "soluprot_score" in candidates[0]
        assert "plddt" in candidates[0], "esmfold pLDDT not surfaced on candidate"
        assert candidates[0]["plddt"] == 85.0
