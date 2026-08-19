import base64

import pytest

from prep import diffdock


# ---------------------------------------------------------------------------
# Security: input validation tests (RED → GREEN via fixes in diffdock.py)
# ---------------------------------------------------------------------------

def test_diffdock_rejects_shell_metachars_in_complex_name():
    with pytest.raises(ValueError):
        diffdock.build_input({"complex_name": "x; rm -rf /", "ligand_smiles": "CCO", "protein_pdb": "ATOM ...\n"})


def test_diffdock_rejects_path_traversal_out_dir():
    with pytest.raises(ValueError):
        diffdock.build_input({"complex_name": "ok", "out_dir": "../../etc", "ligand_smiles": "CCO", "protein_pdb": "ATOM ...\n"})


def test_diffdock_rejects_dotdot_in_config():
    with pytest.raises(ValueError):
        diffdock.build_input({"complex_name": "ok", "config": "../../../etc/passwd", "ligand_smiles": "CCO", "protein_pdb": "ATOM ...\n"})


def test_diffdock_rejects_special_chars_in_complex_name():
    with pytest.raises(ValueError):
        diffdock.build_input({"complex_name": "foo$(cat /etc/passwd)", "ligand_smiles": "CCO", "protein_pdb": "ATOM ...\n"})


def test_diffdock_rejects_spaces_in_complex_name():
    with pytest.raises(ValueError):
        diffdock.build_input({"complex_name": "my complex", "ligand_smiles": "CCO", "protein_pdb": "ATOM ...\n"})


def test_diffdock_csv_quotes_fields():
    out = diffdock.build_input({
        "complex_name": "ok",
        "ligand_smiles": "CCO",
        "protein_pdb": "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\n",
    })
    csv_obj = out["protein_ligand_csv"]
    blob = base64.b64decode(csv_obj["data_b64"]).decode() if isinstance(csv_obj, dict) else csv_obj
    assert "ok" in blob


def test_diffdock_extra_args_ignored_even_if_supplied():
    """extra_args is not portal-exposed; any supplied value must be silently dropped."""
    out = diffdock.build_input({
        "complex_name": "ok",
        "extra_args": "--evil-flag $(rm -rf /)",
        "ligand_smiles": "CCO",
        "protein_pdb": "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\n",
    })
    # Must not appear in cmd
    assert "--evil-flag" not in out.get("cmd", "")
    assert "$(rm" not in out.get("cmd", "")
    # extra_args in output should be empty
    assert out.get("extra_args", "") == ""


def test_diffdock_accepts_valid_complex_name_with_hyphen_underscore():
    """Valid names (alphanumeric, hyphens, underscores) must pass."""
    out = diffdock.build_input({
        "complex_name": "my-complex_01",
        "ligand_smiles": "CCO",
        "protein_pdb": "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\n",
    })
    assert "protein_ligand_csv" in out
    assert out["cmd"]


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


def _archive(files: dict[str, str]) -> dict:
    """Portal-style input_archive (tar.gz, base64) built from name -> text."""
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return {"kind": "uploaded", "file_names": list(files),
            "base64": base64.b64encode(buf.getvalue()).decode()}


def test_diffdock_uses_an_sdf_attached_in_the_upload_archive():
    # The UI builds a worker-ready payload from its own form; an MCP/chat run
    # can only attach files, and the SDF used to be dropped on the floor.
    out = diffdock.build_input({
        "complex_name": "ok",
        "input_archive": _archive({"rec.pdb": "ATOM      1  N\n", "lig.sdf": "  Mrv  \n$$$$\n"}),
    })
    assert [f["filename"] for f in out["sdf_files"]] == ["ok.sdf"]
    assert "Mrv" in base64.b64decode(out["sdf_files"][0]["data_b64"]).decode()
    assert "inputs/ok.sdf" in base64.b64decode(out["protein_ligand_csv"]["data_b64"]).decode()


def test_a_smiles_parameter_still_wins_over_an_attached_sdf():
    out = diffdock.build_input({
        "complex_name": "ok",
        "ligand_smiles": "CCO",
        "input_archive": _archive({"rec.pdb": "ATOM      1  N\n", "lig.sdf": "  Mrv  \n$$$$\n"}),
    })
    assert out["sdf_files"] == []
    assert "CCO" in base64.b64decode(out["protein_ligand_csv"]["data_b64"]).decode()


def test_diffdock_still_fails_loudly_with_no_ligand_at_all():
    with pytest.raises(ValueError, match="ligand"):
        diffdock.build_input({
            "complex_name": "ok",
            "input_archive": _archive({"rec.pdb": "ATOM      1  N\n"}),
        })
