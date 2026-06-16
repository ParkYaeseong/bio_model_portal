import base64, io, tarfile
from packagers import package_proteinmpnn


def _names(b64: str) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(b64))) as t:
        return t.getnames()


def test_proteinmpnn_packager_extracts_fasta_from_real_keys():
    # mirrors the actual worker output: raw_fasta + samples (not "sequences")
    output = {
        "native": {"name": "native", "sequence": "ALSYET"},
        "samples": [
            {"name": "sample_1", "header": "T=0.1, sample=1", "sequence": "GLAYHT"},
            {"name": "sample_2", "header": "T=0.1, sample=2", "sequence": "MKTAYI"},
        ],
        "raw_fasta": ">4KL5\nALSYET\n>sample_1\nGLAYHT\n",
        "stdout_tail": "ok",
    }
    names = _names(package_proteinmpnn(output))
    assert "output.json" in names
    assert "proteinmpnn.fasta" in names      # the full FASTA
    assert "sample_1.fasta" in names and "sample_2.fasta" in names


def test_proteinmpnn_packager_no_crash_when_empty():
    assert "output.json" in _names(package_proteinmpnn({"status": "ok"}))
