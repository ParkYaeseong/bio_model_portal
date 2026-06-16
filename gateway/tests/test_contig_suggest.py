from prep import contig_suggest

def test_ligand_pdb_recommends_motif():
    res = contig_suggest.suggest(open("/tmp/rfd_dbg/4KL5.pdb").read())
    ids = [o["id"] for o in res["options"]]
    assert "custom" in ids
    assert any(i.startswith("chain_") for i in ids)
    rec = [o for o in res["options"] if o["recommended"]]
    assert len(rec) == 1
    assert rec[0]["id"] == "ligand_motif"   # 4KL5 has HETATM ligands
    assert rec[0]["contig"]                 # non-empty motif contig
    assert res["processed_coords"] is True

def test_options_have_required_fields():
    res = contig_suggest.suggest(open("/tmp/rfd_dbg/4KL5.pdb").read())
    for o in res["options"]:
        assert set(["id","label","contig","recommended"]) <= set(o.keys())
