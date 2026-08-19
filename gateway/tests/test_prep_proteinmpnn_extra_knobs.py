from prep import proteinmpnn

_PDB = (
    "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\n"
    "ATOM      2  N   ALA A   2       0.000   0.000   0.000  1.00  0.00           N\n"
    "ATOM      3  N   ALA A   3       0.000   0.000   0.000  1.00  0.00           N\n"
    "ATOM      4  N   ALA B   1       0.000   0.000   0.000  1.00  0.00           N\n"
    "ATOM      5  N   ALA B   2       0.000   0.000   0.000  1.00  0.00           N\n"
)


def test_fixed_positions_text_is_parsed_into_a_per_chain_dict():
    out = proteinmpnn.build_input({"pdb_content": _PDB, "fixed_positions": "A1,A3,B2"})
    assert out["fixed_positions"] == {"A": [1, 3], "B": [2]}


def test_fixed_positions_text_supports_ranges():
    out = proteinmpnn.build_input({"pdb_content": _PDB, "fixed_positions": "A1-3"})
    assert out["fixed_positions"] == {"A": [1, 2, 3]}


def test_fixed_positions_dict_still_accepted_directly():
    out = proteinmpnn.build_input({"pdb_content": _PDB, "fixed_positions": {"A": [1, 2]}})
    assert out["fixed_positions"] == {"A": [1, 2]}


def test_fixed_positions_blank_text_is_dropped():
    out = proteinmpnn.build_input({"pdb_content": _PDB, "fixed_positions": ""})
    assert "fixed_positions" not in out


def test_use_soluble_model_defaults_to_true_when_blank():
    out = proteinmpnn.build_input({"pdb_content": _PDB})
    assert out["use_soluble_model"] is True


def test_use_soluble_model_select_string_true_becomes_bool_true():
    out = proteinmpnn.build_input({"pdb_content": _PDB, "use_soluble_model": "true"})
    assert out["use_soluble_model"] is True


def test_use_soluble_model_select_string_false_becomes_bool_false():
    # A naive `if value:` check would treat the string "false" as truthy -- must not.
    out = proteinmpnn.build_input({"pdb_content": _PDB, "use_soluble_model": "false"})
    assert out["use_soluble_model"] is False


