import base64, io, tarfile
from prep import rfd3

def _archive(pdb_path="/tmp/rfd_dbg/4KL5.pdb"):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        data = open(pdb_path, "rb").read()
        ti = tarfile.TarInfo("4KL5.pdb"); ti.size = len(data)
        t.addfile(ti, io.BytesIO(data))
    return {"base64": base64.b64encode(buf.getvalue()).decode()}

def test_rfd3_input_has_clean_pdb_and_remapped_contig():
    # 4KL5 raw numbering: chain A starts at residue 0, chain B at -2; there is also
    # a numbering jump (orig A201 -> processed A140). A custom contig in original
    # PDB coords must be remapped to the renumbered (1..N, gapless) processed coords.
    out = rfd3.build_input({"input_archive": _archive(), "contigs": "A201-A202"})
    pdb = out["input_files"]["input.pdb"]
    # all ATOM residues must be positively numbered after preprocess
    resseqs = [int(l[22:26]) for l in pdb.splitlines() if l.startswith("ATOM")]
    assert resseqs and min(resseqs) >= 1
    spec = out["inputs"]["spec-1"]
    assert spec["input"] == "input.pdb"
    # each ChainID+resid token is remapped across the numbering jump:
    # original A201 -> processed A140, original A202 -> processed A141
    assert spec["contig"] == "A140-A141"

def test_rfd3_processed_coords_contig_passthrough():
    out = rfd3.build_input({"input_archive": _archive(), "contigs": "A1-140", "contig_processed_coords": True})
    assert out["inputs"]["spec-1"]["contig"] == "A1-140"

def test_rfd3_unconditional_length_only():
    out = rfd3.build_input({"length": 10})
    assert out["inputs"]["spec-1"] == {"length": "10"}

def test_rfd3_custom_range_remaps_both_endpoints():
    out = rfd3.build_input({"input_archive": _archive(), "contigs": "A201-202"})
    contig = out["inputs"]["spec-1"]["contig"]
    # both endpoints must be remapped (no bare original 201/202 left); processed are 140/141
    assert contig == "A140-141", contig
