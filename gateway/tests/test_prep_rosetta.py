import base64, io, tarfile
from prep import rosetta

def _archive(p="/tmp/rfd_dbg/4KL5.pdb"):
    buf=io.BytesIO()
    with tarfile.open(fileobj=buf,mode="w:gz") as t:
        d=open(p,"rb").read(); ti=tarfile.TarInfo("4KL5.pdb"); ti.size=len(d); t.addfile(ti,io.BytesIO(d))
    return {"base64": base64.b64encode(buf.getvalue()).decode()}

def test_rosetta_pdb_content_positive_resseq():
    out = rosetta.build_input({"input_archive": _archive(), "target_id": "4KL5", "nstruct": 1})
    assert out["pdb_content"] and out["target_id"] == "4KL5"
    resseqs = [int(l[22:26]) for l in out["pdb_content"].splitlines() if l.startswith("ATOM")]
    assert resseqs and min(resseqs) >= 1
    assert "input_archive" not in out

def test_rosetta_inline_pdb_passthrough():
    out = rosetta.build_input({"pdb_content": "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\n", "nstruct": 1})
    assert "ATOM" in out["pdb_content"]
