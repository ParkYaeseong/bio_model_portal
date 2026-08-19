import base64, io, tarfile
import pytest
from prep import sequence

def _archive(pdb_path="/tmp/rfd_dbg/4KL5.pdb"):
    buf=io.BytesIO()
    with tarfile.open(fileobj=buf,mode="w:gz") as t:
        d=open(pdb_path,"rb").read(); ti=tarfile.TarInfo("in.pdb"); ti.size=len(d); t.addfile(ti,io.BytesIO(d))
    return {"base64": base64.b64encode(buf.getvalue()).decode()}

def test_bioemu_extracts_sequence_from_uploaded_pdb():
    out = sequence.build_bioemu_input({"input_archive": _archive(), "num_samples": 1})
    assert out["sequence"] and all(c in "ACDEFGHIKLMNPQRSTVWY" for c in out["sequence"])
    assert "input_archive" not in out

def test_bioemu_prefers_typed_sequence_over_pdb():
    out = sequence.build_bioemu_input({"sequence": "MKTAYIAKQR", "input_archive": _archive()})
    assert out["sequence"] == "MKTAYIAKQR"

def test_bioemu_still_errors_when_no_sequence_no_pdb():
    with pytest.raises(ValueError):
        sequence.build_bioemu_input({"num_samples": 1})
