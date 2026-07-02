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


def test_colabfold_fasta_archive_still_raises():
    # Non-AF2 models do not consume a FASTA archive; must still require a sequence.
    with pytest.raises(ValueError):
        sequence.build_folding_input(
            {"input_archive": _fasta_archive("fasta_dir")}, model="ColabFold"
        )


def test_esmfold_fasta_archive_still_raises():
    with pytest.raises(ValueError):
        sequence.build_folding_input(
            {"input_archive": _fasta_archive("fasta_paths")}, model="ESMFold"
        )
