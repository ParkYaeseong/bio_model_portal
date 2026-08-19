from prep import mmseqs

def test_mmseqs_sequence_to_fasta():
    out = mmseqs.build_input({"sequence": "MKTAYIAKQR"})
    assert out.get("query_fasta", "").lstrip().startswith(">")
    assert out.get("task") == "search"
    assert "input_archive" not in out
