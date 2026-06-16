import base64

import pytest

from prep import diffdock


def test_diffdock_smiles_builds_csv():
    out = diffdock.build_input({"complex_name": "t", "ligand_smiles": "CCO",
                                "protein_pdb": "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\n"})
    assert "protein_ligand_csv" in out
    assert "pdb_files" in out
    # SMILES appears as ligand_description in the CSV
    csv = out["protein_ligand_csv"]
    blob = csv["data_b64"] if isinstance(csv, dict) else csv
    if isinstance(csv, dict):
        decoded = base64.b64decode(blob)
        assert b"CCO" in decoded
        assert b"complex_name,protein_path,ligand_description,protein_sequence" in decoded
    # no sdf row when only smiles given
    assert out["sdf_files"] == []
    assert out["cmd"]
    assert out["config"] == "default_inference_args.yaml"
    assert out["data_dir"] == "data"
    assert out["inputs_dir"] == "inputs"


def test_diffdock_sdf_builds_csv_and_sdf_file():
    sdf = "LIG\n  pp\n\n  1  0  0  0  0  0            999 V2000\nM  END\n$$$$\n"
    out = diffdock.build_input({"complex_name": "c1", "ligand_sdf": sdf,
                                "protein_pdb": "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\n"})
    assert len(out["sdf_files"]) == 1
    decoded = base64.b64decode(out["protein_ligand_csv"]["data_b64"])
    # ligand_description points to the inputs/<sdf> path, not raw text
    assert b"inputs/c1.sdf" in decoded


def test_diffdock_requires_ligand():
    with pytest.raises(ValueError):
        diffdock.build_input({"complex_name": "t", "protein_pdb": "ATOM...\n"})


def test_diffdock_requires_protein():
    with pytest.raises(ValueError):
        diffdock.build_input({"complex_name": "t", "ligand_smiles": "CCO"})


def test_diffdock_passthrough_preformed():
    pre = {"protein_ligand_csv": {"filename": "x.csv", "data_b64": "abc"}, "cmd": "python", "pdb_files": []}
    out = diffdock.build_input(dict(pre))
    assert out.get("protein_ligand_csv") == pre["protein_ligand_csv"]
    assert out.get("cmd") == "python"
    # internal-only keys stripped on passthrough
    assert "input_archive" not in out
