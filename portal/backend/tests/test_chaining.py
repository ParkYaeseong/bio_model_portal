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
    assert {"from": "rfdiffusion", "to": "diffdock", "role": "structure"} in g["edges"]
    assert all(e["from"] != e["to"] for e in g["edges"])
    assert any(ex["steps"][0] == "rfdiffusion" for ex in g["examples"])


def test_first_fasta_sequence():
    text = ">seq1\nACDEF\nGHIKL\n>seq2\nZZZZ\n"
    assert chaining._first_fasta_sequence(text) == "ACDEFGHIKL"
