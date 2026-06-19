from prep import proteinmpnn, rosetta, sequence
from prep.defaults import MODEL_DEFAULTS, apply_defaults

_MINI_PDB = (
    "ATOM      1  N   MET A   1      0.000   0.000   0.000  1.00  0.00           N\n"
    "ATOM      2  CA  MET A   1      1.000   1.000   1.000  1.00  0.00           C\n"
    "ATOM      3  C   MET A   1      2.000   2.000   2.000  1.00  0.00           C\n"
)


def test_apply_defaults_fills_when_absent():
    out = apply_defaults({}, "proteinmpnn")
    assert out["num_seq_per_target"] == 1
    assert out["sampling_temp"] == 0.1
    assert out["batch_size"] == 1
    assert out["backbone_noise"] == 0.0


def test_apply_defaults_does_not_override_provided():
    out = apply_defaults({"sampling_temp": 0.3, "num_seq_per_target": 16}, "proteinmpnn")
    assert out["sampling_temp"] == 0.3
    assert out["num_seq_per_target"] == 16


def test_apply_defaults_treats_blank_string_as_absent():
    out = apply_defaults({"sampling_temp": "", "num_seq_per_target": "  "}, "proteinmpnn")
    assert out["sampling_temp"] == 0.1
    assert out["num_seq_per_target"] == 1


def test_apply_defaults_unknown_model_is_noop():
    assert apply_defaults({"a": 1}, "nope") == {"a": 1}


def test_proteinmpnn_build_input_pins_defaults():
    out = proteinmpnn.build_input({"pdb_text": _MINI_PDB, "pdb_name": "x"})
    assert out["num_seq_per_target"] == 1
    assert out["sampling_temp"] == 0.1
    assert out["batch_size"] == 1
    assert out["backbone_noise"] == 0.0


def test_proteinmpnn_respects_user_values():
    out = proteinmpnn.build_input(
        {"pdb_text": _MINI_PDB, "num_seq_per_target": 8, "sampling_temp": 0.2}
    )
    assert out["num_seq_per_target"] == 8
    assert out["sampling_temp"] == 0.2


def test_rosetta_default_nstruct():
    out = rosetta.build_input({"pdb_content": _MINI_PDB})
    assert out["nstruct"] == 1


def test_colabfold_pins_defaults():
    out = sequence.build_folding_input({"sequence": "MKTAYIAK"}, model="ColabFold")
    assert out["num_recycle"] == 3
    assert out["num_models"] == 5
    assert out["msa_mode"] == "mmseqs2_uniref_env"


def test_esmfold_has_no_pinned_numeric_defaults():
    # ESMFold worker param names are unverified; do not inject keys it may reject.
    out = sequence.build_folding_input({"sequence": "MKTAYIAK"}, model="ESMFold")
    assert "num_recycle" not in out and "num_models" not in out


def test_alphafold_not_overridden_by_colabfold_defaults():
    out = sequence.build_folding_input({"sequence": "MKTAYIAK"}, model="AlphaFold2")
    assert "num_recycle" not in out


def test_bioemu_pins_defaults():
    out = sequence.build_bioemu_input({"sequence": "MKTAYIAK"})
    assert out["num_samples"] == 10
    assert out["model_name"] == "bioemu-v1.1"
