"""Every model's input contract, checked at submit time instead of in the
gateway adapter minutes later."""
import json
from pathlib import Path

import pytest

from app.mcp.validation import validate_run


def _pdb(tmp_path, name="ab.pdb"):
    p = tmp_path / name
    p.write_text("ATOM      1  N   ALA A   1      0.000   0.000   0.000\n")
    return p


def _fasta(tmp_path, name="q.fasta", text=">a\nACDEFGHIKL\n"):
    p = tmp_path / name
    p.write_text(text)
    return p


# --- structure-input models -------------------------------------------------

@pytest.mark.parametrize("pipeline", ["antifold", "proteinmpnn", "ppiformer", "rosetta_relax"])
def test_structure_models_reject_a_run_with_no_structure(pipeline):
    with pytest.raises(ValueError) as exc:
        validate_run(pipeline, {"mutations": "YH33W"}, None, [])
    assert ".pdb" in str(exc.value)
    assert "from_job_id" in str(exc.value)


def test_structure_models_reject_a_non_structure_upload(tmp_path):
    fasta = _fasta(tmp_path)
    with pytest.raises(ValueError) as exc:
        validate_run("antifold", {}, None, [fasta])
    assert "q.fasta" in str(exc.value)


def test_antifold_accepts_a_pdb(tmp_path):
    validate_run("antifold", {"heavy_chain": "H", "light_chain": "L"}, None, [_pdb(tmp_path)])


def test_antifold_rejects_unknown_region_tokens(tmp_path):
    with pytest.raises(ValueError, match="region token"):
        validate_run("antifold", {"regions": "CDR9"}, None, [_pdb(tmp_path)])


def test_antifold_rejects_a_multi_character_chain_id(tmp_path):
    with pytest.raises(ValueError, match="single PDB chain id"):
        validate_run("antifold", {"heavy_chain": "heavy"}, None, [_pdb(tmp_path)])


def test_antifold_rejects_the_same_chain_twice(tmp_path):
    with pytest.raises(ValueError, match="different chains"):
        validate_run("antifold", {"heavy_chain": "A", "antigen_chain": "A"}, None, [_pdb(tmp_path)])


def test_ppiformer_requires_mutations(tmp_path):
    with pytest.raises(ValueError, match="mutations"):
        validate_run("ppiformer", {}, None, [_pdb(tmp_path)])
    validate_run("ppiformer", {"mutations": "YH33W"}, None, [_pdb(tmp_path)])


# --- sequence-input models --------------------------------------------------

@pytest.mark.parametrize("pipeline", ["alphafold3", "boltz2", "colabfold", "esmfold", "bioemu"])
def test_folding_models_require_a_sequence(pipeline):
    params = {"api_key": "k", "model_preset": "monomer", "db_preset": "full_dbs"}
    with pytest.raises(ValueError, match="needs a protein sequence"):
        validate_run(pipeline, params, None, [])


def test_folding_models_accept_an_uploaded_structure_or_fasta(tmp_path):
    validate_run("boltz2", {}, None, [_pdb(tmp_path)])
    validate_run("alphafold3", {}, None, [_fasta(tmp_path)])


def test_sequence_with_non_amino_acid_characters_is_rejected():
    with pytest.raises(ValueError, match="not amino"):
        validate_run("esmfold", {}, "/opt/data/protein.pdb", [])


def test_multimer_colon_sequence_is_accepted():
    validate_run("boltz2", {}, "ACDEFGHIKL:MNPQRSTVWY", [])


# --- model-specific rules ---------------------------------------------------

def test_alphafold3_raw_json_replaces_the_sequence_requirement():
    validate_run("alphafold3", {"af3_json": json.dumps({"name": "x", "sequences": []})}, None, [])


def test_alphafold3_rejects_malformed_json():
    with pytest.raises(ValueError, match="not valid JSON"):
        validate_run("alphafold3", {"af3_json": "{not json"}, None, [])


def test_boltz2_affinity_needs_a_ligand():
    with pytest.raises(ValueError, match="ligand_smiles"):
        validate_run("boltz2", {"predict_affinity": "true"}, "ACDEFGHIKL", [])
    validate_run("boltz2", {"predict_affinity": "true", "ligand_smiles": "CCO"}, "ACDEFGHIKL", [])


def test_select_parameters_must_use_a_catalog_value():
    with pytest.raises(ValueError, match="must be one of"):
        validate_run("colabfold", {"msa_mode": "mmseqs2"}, "ACDEFGHIKL", [])
    validate_run("colabfold", {"msa_mode": "mmseqs2_uniref_env"}, "ACDEFGHIKL", [])


def test_number_parameters_are_range_checked():
    with pytest.raises(ValueError, match="must be >= 1"):
        validate_run("boltz2", {"recycling_steps": 0}, "ACDEFGHIKL", [])
    with pytest.raises(ValueError, match="must be a number"):
        validate_run("boltz2", {"recycling_steps": "three"}, "ACDEFGHIKL", [])


def test_alphafold_multimer_needs_more_than_one_chain(tmp_path):
    params = {"model_preset": "multimer", "db_preset": "full_dbs", "max_template_date": "2023-09-01"}
    with pytest.raises(ValueError, match="at least two chains"):
        validate_run("alphafold", dict(params), "ACDEFGHIKL", [])
    two = _fasta(tmp_path, "pair.fasta", ">a\nACDEFGHIKL\n>b\nMNPQRSTVWY\n")
    validate_run("alphafold", dict(params), None, [two])


def test_alphafold_selects_have_no_default_and_must_be_supplied():
    with pytest.raises(ValueError, match="model_preset"):
        validate_run("alphafold", {}, "ACDEFGHIKL", [])


def test_required_param_with_a_recommended_default_is_filled_in_and_reported():
    params = {"length": "100"}
    applied = validate_run("rfdiffusion", params, None, [])
    assert applied == {"num_designs": "1"}
    assert params["num_designs"] == "1"


def test_rfdiffusion_still_needs_a_design_spec():
    with pytest.raises(ValueError, match="design spec"):
        validate_run("rfdiffusion", {}, "ACDEFGHIKL", [])


def test_diffdock_needs_a_ligand(tmp_path):
    with pytest.raises(ValueError, match="needs a ligand"):
        validate_run("diffdock", {}, None, [_pdb(tmp_path)])
    validate_run("diffdock", {"ligand_smiles": "CCO"}, None, [_pdb(tmp_path)])
    sdf = tmp_path / "lig.sdf"; sdf.write_text("mol\n")
    validate_run("diffdock", {}, None, [_pdb(tmp_path), sdf])


def test_phastest_parameters_are_validated(tmp_path):
    genome = tmp_path / "g.fasta"; genome.write_text(">c\nACGT\n")
    with pytest.raises(ValueError, match="input_type"):
        validate_run("phastest", {}, None, [genome])
    with pytest.raises(ValueError, match="mode"):
        validate_run("phastest", {"input_type": "fasta"}, None, [genome])
    with pytest.raises(ValueError, match="sample_name"):
        validate_run("phastest", {"input_type": "fasta", "mode": "lite"}, None, [genome])
    with pytest.raises(ValueError, match="needs an uploaded genome"):
        validate_run("phastest", {"input_type": "fasta", "mode": "lite", "sample_name": "s"}, None, [])
    with pytest.raises(ValueError, match="accession"):
        validate_run("phastest", {"input_type": "genbank", "mode": "lite", "sample_name": "s"}, None, [])
    validate_run("phastest", {"input_type": "fasta", "mode": "lite", "sample_name": "s"}, None, [genome])


def test_esmfold2_requires_the_users_api_key():
    with pytest.raises(ValueError, match="api_key"):
        validate_run("esmfold2", {}, "ACDEFGHIKL", [])
