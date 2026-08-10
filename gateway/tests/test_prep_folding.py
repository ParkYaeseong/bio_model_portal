import base64, io, tarfile
import pytest
from prep import sequence

def _archive(p="/tmp/rfd_dbg/4KL5.pdb"):
    buf=io.BytesIO()
    with tarfile.open(fileobj=buf,mode="w:gz") as t:
        d=open(p,"rb").read(); ti=tarfile.TarInfo("in.pdb"); ti.size=len(d); t.addfile(ti,io.BytesIO(d))
    return {"base64": base64.b64encode(buf.getvalue()).decode()}

def test_folding_extracts_sequence_from_pdb():
    out = sequence.build_folding_input({"input_archive": _archive()}, model="ESMFold")
    assert out["sequence"] and len(out["sequence"]) > 50
    assert "input_archive" not in out


def _multichain_pdb_archive():
    body = (
        "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\n"
        "ATOM      2  N   ALA A   2       0.000   0.000   0.000  1.00  0.00           N\n"
        "ATOM      3  N   GLY B   1       0.000   0.000   0.000  1.00  0.00           N\n"
    ).encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        ti = tarfile.TarInfo("complex.pdb")
        ti.size = len(body)
        t.addfile(ti, io.BytesIO(body))
    return {"base64": base64.b64encode(buf.getvalue()).decode()}


def test_colabfold_multichain_pdb_upload_keeps_every_chain():
    # Before the fix this silently collapsed to just the longest chain.
    out = sequence.build_folding_input({"input_archive": _multichain_pdb_archive()}, model="ColabFold")
    assert ":" in out["sequence"]
    assert out["sequence"].count(":") == 1


def test_alphafold3_multichain_pdb_upload_keeps_every_chain():
    out = sequence.build_folding_input({"input_archive": _multichain_pdb_archive()}, model="AlphaFold3")
    assert ":" in out["sequence"]


def test_esmfold_multichain_pdb_upload_still_takes_only_the_longest_chain():
    # ESMFold can't fold a ':'-joined complex -- must keep the old behavior.
    out = sequence.build_folding_input({"input_archive": _multichain_pdb_archive()}, model="ESMFold")
    assert ":" not in out["sequence"]


def test_alphafold2_multichain_pdb_upload_still_takes_only_the_longest_chain():
    # AF2's multimer convention is a separate FASTA-archive/'/' path, not a
    # colon-joined sequence -- must not get one it would just reject.
    out = sequence.build_folding_input({"input_archive": _multichain_pdb_archive()}, model="AlphaFold2")
    assert ":" not in out["sequence"]

def test_folding_passthrough_typed_sequence():
    out = sequence.build_folding_input({"sequence":"MKTAYIAKQR","model_preset":"monomer"}, model="ColabFold")
    assert out["sequence"]=="MKTAYIAKQR" and out["model_preset"]=="monomer"

def test_colabfold_multimer_colon_sequence_survives():
    # A ':'-joined multimer sequence must reach the ColabFold worker verbatim
    # (the worker auto-runs alphafold2_multimer_v3 when it sees the colon).
    seq = "MKTAYIAKQR:GGGSGGGSAA"
    out = sequence.build_folding_input({"sequence": seq}, model="ColabFold")
    assert out["sequence"] == seq
    assert ":" in out["sequence"]

def test_folding_errors_when_no_input():
    with pytest.raises(ValueError):
        sequence.build_folding_input({}, model="ESMFold")


def _fasta_archive(kind, name="sequence_multimer_input.fasta"):
    # A real tar.gz carrying a FASTA file (no PDB), as the portal builds for AF2 multimer.
    buf = io.BytesIO()
    data = b">A\nMKTAYIAKQR\n>B\nGGGSGGGS\n"
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        ti = tarfile.TarInfo(name); ti.size = len(data); t.addfile(ti, io.BytesIO(data))
    arc = {"base64": base64.b64encode(buf.getvalue()).decode(), "kind": kind}
    if kind == "fasta_dir":
        arc["file_names"] = [name]
    return arc


def test_af2_multimer_fasta_dir_archive_passes_through():
    archive = _fasta_archive("fasta_dir")
    out = sequence.build_folding_input({"input_archive": archive, "model_preset": "multimer"},
                                       model="AlphaFold2")
    assert out["input_archive"] == archive
    assert out["model_preset"] == "multimer"
    assert not str(out.get("sequence", "")).strip()  # no inline sequence invented


def test_af2_multimer_fasta_paths_archive_passes_through():
    archive = _fasta_archive("fasta_paths")
    out = sequence.build_folding_input({"input_archive": archive}, model="AlphaFold2")
    assert out["input_archive"] == archive


def test_af2_monomer_inline_sequence_pops_archive():
    out = sequence.build_folding_input(
        {"sequence": "MKTAYIAK", "input_archive": _fasta_archive("fasta_dir")},
        model="AlphaFold2",
    )
    assert out["sequence"] == "MKTAYIAK"
    assert "input_archive" not in out


def _uploaded_fasta_archive(body: bytes, name="input.fasta"):
    # A tar.gz carrying a FASTA, tagged kind='uploaded' exactly as the portal
    # builds it for a ColabFold/ESMFold file upload (NOT the AF2 fasta_dir tag).
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        ti = tarfile.TarInfo(name); ti.size = len(body); t.addfile(ti, io.BytesIO(body))
    return {
        "base64": base64.b64encode(buf.getvalue()).decode(),
        "kind": "uploaded",
        "file_names": [name],
    }


def test_colabfold_fasta_upload_multimer_becomes_colon_sequence():
    # Two records => a multimer; ColabFold auto-runs multimer when it sees ':'.
    archive = _uploaded_fasta_archive(b">A\nMKTAYIAKQR\n>B\nGGGSGGGS\n")
    out = sequence.build_folding_input({"input_archive": archive}, model="ColabFold")
    assert out["sequence"] == "MKTAYIAKQR:GGGSGGGS"
    assert "input_archive" not in out


def test_colabfold_fasta_upload_monomer_single_record():
    archive = _uploaded_fasta_archive(b">only\nMKTAYIAKQR\n")
    out = sequence.build_folding_input({"input_archive": archive}, model="ColabFold")
    assert out["sequence"] == "MKTAYIAKQR"
    assert ":" not in out["sequence"]


def test_esmfold_fasta_upload_monomer_single_record():
    # ESMFold is single-chain; a one-record FASTA folds as a monomer.
    archive = _uploaded_fasta_archive(b">only\nMKTAYIAKQR\n")
    out = sequence.build_folding_input({"input_archive": archive}, model="ESMFold")
    assert out["sequence"] == "MKTAYIAKQR"
    assert "input_archive" not in out


def test_esmfold_fasta_upload_multimer_rejected():
    # The ESMFold worker (facebook/esmfold_v1) can't fold a ':'-joined complex —
    # it must be rejected here, not sent to the worker (which 500s on the colon).
    archive = _uploaded_fasta_archive(b">A\nMKTAYIAKQR\n>B\nGGGSGGGS\n")
    with pytest.raises(ValueError):
        sequence.build_folding_input({"input_archive": archive}, model="ESMFold")


def test_folding_headerless_fasta_is_one_bare_sequence():
    archive = _uploaded_fasta_archive(b"MKTAYIAKQR\n", name="seq.fasta")
    out = sequence.build_folding_input({"input_archive": archive}, model="ColabFold")
    assert out["sequence"] == "MKTAYIAKQR"


def test_folding_still_errors_when_archive_has_no_sequence():
    # An archive with neither PDB nor FASTA must still fail fast.
    buf = io.BytesIO()
    d = b"# just a note"
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        ti = tarfile.TarInfo("readme.md"); ti.size = len(d); t.addfile(ti, io.BytesIO(d))
    archive = {"base64": base64.b64encode(buf.getvalue()).decode(), "kind": "uploaded"}
    with pytest.raises(ValueError):
        sequence.build_folding_input({"input_archive": archive}, model="ColabFold")


def test_af3_json_textarea_is_parsed_and_bypasses_sequence_requirement():
    raw = '{"name": "j", "sequences": [{"protein": {"id": "A", "sequence": "MKT"}}], "modelSeeds": [1], "dialect": "alphafold3", "version": 3}'
    out = sequence.build_folding_input({"af3_json": raw}, model="AlphaFold3")
    assert out["af3_json"] == {
        "name": "j",
        "sequences": [{"protein": {"id": "A", "sequence": "MKT"}}],
        "modelSeeds": [1],
        "dialect": "alphafold3",
        "version": 3,
    }
    assert "sequence" not in out


def test_af3_json_dict_passthrough_bypasses_sequence_requirement():
    payload_json = {"name": "j", "sequences": []}
    out = sequence.build_folding_input({"af3_json": payload_json}, model="AlphaFold3")
    assert out["af3_json"] == payload_json


def test_af3_json_blank_textarea_is_dropped_and_falls_back_to_sequence():
    out = sequence.build_folding_input({"af3_json": "  ", "sequence": "MKT"}, model="AlphaFold3")
    assert "af3_json" not in out
    assert out["sequence"] == "MKT"


def test_af3_json_invalid_json_raises():
    with pytest.raises(ValueError):
        sequence.build_folding_input({"af3_json": "{not valid json"}, model="AlphaFold3")


def test_af3_json_non_object_raises():
    with pytest.raises(ValueError):
        sequence.build_folding_input({"af3_json": "[1, 2, 3]"}, model="AlphaFold3")


def test_af2_extra_flags_renamed_to_alphafold_extra_flags():
    # portal historically sent the free-text field as extra_flags; the worker
    # only reads alphafold_extra_flags.
    out = sequence.assemble_alphafold_flags(
        {"sequence": "MKT", "model_preset": "monomer", "extra_flags": "--benchmark"}
    )
    assert "extra_flags" not in out
    assert out["alphafold_extra_flags"] == "--benchmark"


def test_af2_knobs_become_flags():
    out = sequence.assemble_alphafold_flags(
        {
            "sequence": "MKT",
            "model_preset": "multimer",
            "models_to_relax": "none",
            "num_multimer_predictions_per_model": 3,
        }
    )
    assert "models_to_relax" not in out and "num_multimer_predictions_per_model" not in out
    f = out["alphafold_extra_flags"]
    assert "--models_to_relax=none" in f and "--num_multimer_predictions_per_model=3" in f


def test_af2_blank_knobs_ignored():
    out = sequence.assemble_alphafold_flags(
        {"sequence": "MKT", "models_to_relax": "", "num_multimer_predictions_per_model": ""}
    )
    assert "alphafold_extra_flags" not in out


def test_af2_bad_prediction_count_ignored():
    out = sequence.assemble_alphafold_flags(
        {"sequence": "MKT", "num_multimer_predictions_per_model": "abc"}
    )
    assert "alphafold_extra_flags" not in out


def test_af2_user_flag_wins_over_knob():
    out = sequence.assemble_alphafold_flags(
        {"sequence": "MKT", "models_to_relax": "all", "extra_flags": "--models_to_relax=none"}
    )
    # knob is skipped because the user typed the flag explicitly
    assert out["alphafold_extra_flags"].count("--models_to_relax") == 1
    assert "--models_to_relax=none" in out["alphafold_extra_flags"]


def test_af2_adapter_multimer_archive_preserved_with_flags():
    from adapters import adapter_alphafold
    out = adapter_alphafold(
        {
            "pipeline": "alphafold",
            "model_preset": "multimer",
            "models_to_relax": "none",
            "input_archive": _fasta_archive("fasta_paths"),
        }
    )
    assert "input_archive" in out  # multimer FASTA archive survives
    assert "--models_to_relax=none" in out["alphafold_extra_flags"]
