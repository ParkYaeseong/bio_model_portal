# tests/test_selfimprove_feedback.py
from app import models
from app.database import SessionLocal
from app.selfimprove import feedback


def test_active_artifact_returns_only_active():
    with SessionLocal() as db:
        db.add(models.ImprovementArtifact(status="proposed", summary="p", payload={}, stats={}))
        active = models.ImprovementArtifact(status="active", summary="a",
            payload={"recommended_defaults": {"esmfold": {"num_recycle": 3}},
                     "warnings": [{"pipeline": "proteinmpnn", "condition": "2/2 failed", "message": "no PDB"}],
                     "recipes": []}, stats={})
        db.add(active); db.commit()
        got = feedback.active_artifact(db)
        assert got is not None and got.status == "active"


def test_render_prompt_block_includes_defaults_and_warnings():
    art = models.ImprovementArtifact(status="active", summary="a", payload={
        "recommended_defaults": {"esmfold": {"num_recycle": 3}},
        "warnings": [{"pipeline": "proteinmpnn", "condition": "2/2 failed", "message": "no PDB content"}],
        "recipes": [{"goal": "rfdiffusion → proteinmpnn", "steps": ["rfdiffusion", "proteinmpnn"]}],
    }, stats={})
    block = feedback.render_prompt_block(art)
    assert "esmfold" in block and "num_recycle" in block
    assert "proteinmpnn" in block and "no PDB content" in block
    assert "rfdiffusion → proteinmpnn" in block


def test_render_prompt_block_none_is_empty():
    assert feedback.render_prompt_block(None) == ""
