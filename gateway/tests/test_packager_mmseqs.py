import base64, gzip, io, tarfile
from packagers import package_mmseqs


def _members(b64: str) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(b64))) as t:
        for m in t.getmembers():
            f = t.extractfile(m)
            out[m.name] = f.read() if f else b""
    return out


def test_mmseqs_packager_extracts_tsv_and_a3m():
    a3m_text = ">query\nMKTAYIAKQR\n>hit1\nMKTAYIAKQS\n"
    output = {
        "task": "search",
        "tsv": "query\thit1\t99.0\n",
        "a3m_gz_b64": base64.b64encode(gzip.compress(a3m_text.encode())).decode(),
        "hit_count": 1,
    }
    members = _members(package_mmseqs(output))
    assert "output.json" in members
    assert "search.tsv" in members
    assert b"hit1" in members["search.tsv"]
    assert "msa.a3m" in members
    assert members["msa.a3m"].decode() == a3m_text


def test_mmseqs_packager_handles_empty_results():
    # No hits: empty tsv string + empty (but valid) a3m gz -> no crash, json present.
    output = {"task": "search", "tsv": "", "a3m_gz_b64": base64.b64encode(gzip.compress(b"")).decode()}
    members = _members(package_mmseqs(output))
    assert "output.json" in members
    assert "search.tsv" not in members  # empty tsv not materialized
