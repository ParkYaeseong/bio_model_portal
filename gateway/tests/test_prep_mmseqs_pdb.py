import base64, io, tarfile
from prep import mmseqs

def _archive(pdb_path="/tmp/rfd_dbg/4KL5.pdb"):
    buf=io.BytesIO()
    with tarfile.open(fileobj=buf,mode="w:gz") as t:
        d=open(pdb_path,"rb").read(); ti=tarfile.TarInfo("in.pdb"); ti.size=len(d); t.addfile(ti,io.BytesIO(d))
    return {"base64": base64.b64encode(buf.getvalue()).decode()}

def test_mmseqs_extracts_fasta_from_uploaded_pdb():
    out = mmseqs.build_input({"input_archive": _archive()})
    assert out["query_fasta"].startswith(">")
    assert len(out["query_fasta"].splitlines()[1]) > 50   # a real sequence
    assert out["task"] == "search"
    assert "input_archive" not in out

def test_mmseqs_typed_sequence_still_works():
    out = mmseqs.build_input({"sequence": "MKTAYIAKQR"})
    assert ">query" in out["query_fasta"] and "MKTAYIAKQR" in out["query_fasta"]
