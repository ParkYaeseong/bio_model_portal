import types

import pytest

from app import chaining


def test_compatible_role_structure_files():
    assert chaining.compatible_role("rfdiffusion", "proteinmpnn") == ("structure", "files")
    assert chaining.compatible_role("colabfold", "diffdock") == ("structure", "files")


def test_compatible_role_sequence():
    assert chaining.compatible_role("proteinmpnn", "colabfold") == ("sequence", "sequence")


def test_incompatible_pair_returns_none():
    assert chaining.compatible_role("rfdiffusion", "mmseqs") is None
    assert chaining.compatible_role("diffdock", "colabfold") is None


def test_compatible_targets_lists_downstream():
    targets = chaining.compatible_targets("rfdiffusion")
    assert {"proteinmpnn", "diffdock", "rosetta_relax"}.issubset(set(targets))
    assert "rfdiffusion" not in targets  # no self


def test_compat_graph_shape():
    g = chaining.compat_graph()
    assert {n["key"] for n in g["nodes"]} >= {"rfdiffusion", "diffdock", "colabfold", "proteinmpnn"}
    assert {"from": "rfdiffusion", "to": "diffdock", "role": "structure", "advanced": False} in g["edges"]
    # Feeding a structure back into RFdiffusion is flagged advanced.
    assert {"from": "colabfold", "to": "rfdiffusion", "role": "structure", "advanced": True} in g["edges"]
    assert all(e["from"] != e["to"] for e in g["edges"])
    assert any(ex["steps"][0] == "rfdiffusion" for ex in g["examples"])


def test_first_fasta_sequence():
    text = ">seq1\nACDEF\nGHIKL\n>seq2\nZZZZ\n"
    assert chaining._first_fasta_sequence(text) == "ACDEFGHIKL"


def _job(pipeline, artifacts):
    return types.SimpleNamespace(pipeline=pipeline, artifacts=artifacts)


def _art(id, file_name, kind="structure", file_path=""):
    return types.SimpleNamespace(id=id, file_name=file_name, kind=kind, file_path=file_path)


def test_plan_chain_files_delivery_selects_structure():
    src = _job("rfdiffusion", [_art("a1", "backbone.pdb", "structure"), _art("a2", "log.txt", "log")])
    plan = chaining.plan_chain(None, src, "diffdock")
    assert plan.delivery == "files"
    assert [a.file_name for a in plan.artifacts] == ["backbone.pdb"]


def test_plan_chain_sequence_delivery(tmp_path):
    fa = tmp_path / "designs.fasta"; fa.write_text(">d1\nACDEF\n")
    src = _job("proteinmpnn", [_art("a1", "designs.fasta", "generic", str(fa))])
    plan = chaining.plan_chain(None, src, "colabfold")
    assert plan.delivery == "sequence"
    assert plan.sequence == "ACDEF"


def test_plan_chain_explicit_ids_override():
    src = _job("rfdiffusion", [_art("a1", "pick.pdb"), _art("a2", "skip.pdb")])
    plan = chaining.plan_chain(None, src, "diffdock", source_artifact_ids=["a1"])
    assert [a.file_name for a in plan.artifacts] == ["pick.pdb"]


def test_plan_chain_explicit_ids_missing():
    src = _job("rfdiffusion", [_art("a1", "pick.pdb")])
    with pytest.raises(chaining.ChainError, match="not found"):
        chaining.plan_chain(None, src, "diffdock", source_artifact_ids=["zzz"])


def test_plan_chain_explicit_ids_requires_files_target():
    src = _job("rfdiffusion", [_art("a1", "pick.pdb")])
    with pytest.raises(chaining.ChainError, match="does not accept file inputs"):
        chaining.plan_chain(None, src, "colabfold", source_artifact_ids=["a1"])


def test_plan_chain_incompatible_pair():
    src = _job("rfdiffusion", [_art("a1", "b.pdb")])
    with pytest.raises(chaining.ChainError, match="cannot feed"):
        chaining.plan_chain(None, src, "mmseqs")


def test_plan_chain_no_structure_artifacts():
    src = _job("rfdiffusion", [_art("a1", "log.txt", "log")])
    with pytest.raises(chaining.ChainError, match="no structure"):
        chaining.plan_chain(None, src, "diffdock")


def test_plan_chain_no_fasta():
    src = _job("proteinmpnn", [_art("a1", "notes.txt", "generic")])
    with pytest.raises(chaining.ChainError, match="no FASTA"):
        chaining.plan_chain(None, src, "colabfold")


def test_plan_chain_explicit_ids_wrong_kind():
    src = _job("rfdiffusion", [_art("a1", "stdout.log", "log")])
    with pytest.raises(chaining.ChainError, match="not valid file inputs"):
        chaining.plan_chain(None, src, "diffdock", source_artifact_ids=["a1"])


def test_designed_sequences_can_feed_the_newer_folding_models():
    # AF3/Boltz-2/AntiFold/PPIformer/ANARCII arrived after the first chaining
    # pass and were unreachable from `from_job_id` until they were registered.
    assert chaining.compatible_role("proteinmpnn", "alphafold3") == ("sequence", "sequence")
    assert chaining.compatible_role("proteinmpnn", "boltz2") == ("sequence", "sequence")
    assert chaining.compatible_role("antifold", "colabfold") == ("sequence", "sequence")
    assert chaining.compatible_role("proteinmpnn", "anarcii") == ("sequence", "sequence")


def test_predicted_structures_can_feed_structure_consumers():
    assert chaining.compatible_role("alphafold3", "ppiformer") == ("structure", "files")
    assert chaining.compatible_role("boltz2", "antifold") == ("structure", "files")
    assert chaining.compatible_role("colabfold", "antifold") == ("structure", "files")
    assert "ppiformer" in chaining.compatible_targets("colabfold")


def test_multi_chain_design_is_converted_to_the_folding_convention():
    # ProteinMPNN/AntiFold separate chains with '/'; ColabFold/AF3/Boltz-2 want
    # ':' and AF3 rejects the raw '/' string outright.
    text = ">T=0.2, sample=1\nEVQLVESG/DIQMTQSP\n"
    assert chaining._first_fasta_sequence(text) == "EVQLVESG:DIQMTQSP"


def test_a_trailing_chain_break_does_not_become_an_empty_chain():
    assert chaining._first_fasta_sequence(">d\nACDEFG/\n") == "ACDEFG"
