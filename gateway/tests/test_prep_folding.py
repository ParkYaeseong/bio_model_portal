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

def test_folding_errors_when_no_input():
    with pytest.raises(ValueError):
        sequence.build_folding_input({}, model="ESMFold")
