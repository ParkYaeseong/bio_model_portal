import uuid
from pathlib import Path

from app import models
from app.workflow import orchestrator


def _job(tmp_path, pipeline):
    d = tmp_path / uuid.uuid4().hex
    d.mkdir()
    j = models.Job(user_id=1, title="t", pipeline=pipeline, status="completed")
    j.result_dir = str(d)
    return j, d


def test_collect_proteinmpnn_parses_fasta(tmp_path):
    j, d = _job(tmp_path, "proteinmpnn")
    (d / "designs.fa").write_text(">d1\nMKTAYIA\n>d2\nGGGSGGG\n")
    result, metrics = orchestrator._collect_worker_output(j)
    assert metrics["designs"] == 2
    assert result["candidates"][0] == {"id": "d1", "sequence": "MKTAYIA"}


def test_collect_esmfold_mean_plddt(tmp_path):
    j, d = _job(tmp_path, "esmfold")
    (d / "ranked_0.pdb").write_text(
        "ATOM      1 CA   ALA A   1       0.000   0.000   0.000  1.00 80.00           C\n"
        "ATOM      2 CA   GLY A   2       0.000   0.000   0.000  1.00 90.00           C\n"
    )
    result, metrics = orchestrator._collect_worker_output(j)
    assert result["structures"] and metrics["plddt"] == 85.0


def test_collect_mmseqs_parses_a3m(tmp_path):
    j, d = _job(tmp_path, "mmseqs")
    (d / "out.a3m").write_text(">q\nACDEFG\n>h1\nACDEFG\n")
    result, metrics = orchestrator._collect_worker_output(j)
    assert result["msa"] == ["ACDEFG", "ACDEFG"] and metrics["n_seqs"] == 2
