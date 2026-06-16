import base64, io, tarfile
from prep import proteinmpnn

def _archive(p="/tmp/rfd_dbg/4KL5.pdb"):
    buf=io.BytesIO()
    with tarfile.open(fileobj=buf,mode="w:gz") as t:
        d=open(p,"rb").read(); ti=tarfile.TarInfo("4KL5.pdb"); ti.size=len(d); t.addfile(ti,io.BytesIO(d))
    return {"base64": base64.b64encode(buf.getvalue()).decode()}

def test_proteinmpnn_pdb_base64_positive_resseq():
    out = proteinmpnn.build_input({"input_archive": _archive(), "pdb_name": "4KL5"})
    assert "pdb_base64" in out and out["pdb_name"] == "4KL5"
    pdb = base64.b64decode(out["pdb_base64"]).decode()
    resseqs = [int(l[22:26]) for l in pdb.splitlines() if l.startswith("ATOM")]
    assert resseqs and min(resseqs) >= 1
    assert "input_archive" not in out
