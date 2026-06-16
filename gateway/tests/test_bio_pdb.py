import os
from bio import pdb

PDB = "/tmp/rfd_dbg/4KL5.pdb"  # chain B has residues -2,-1,0 (the bug fixture)

def test_strip_nonpositive_removes_negative_residues():
    text = open(PDB).read()
    before = pdb.residues_by_chain(text)
    assert any(r.resseq <= 0 for r in before.get("B", [])), "fixture should have non-positive residues"
    out, mapping = pdb.preprocess_pdb(text, strip_nonpositive_resseq=True, renumber_resseq_from_1=True)
    after = pdb.residues_by_chain(out)
    assert all(r.resseq >= 1 for chain in after.values() for r in chain), "all residues positive after preprocess"
    assert "B" in mapping and mapping["B"][0]["processed_resseq"] >= 1
