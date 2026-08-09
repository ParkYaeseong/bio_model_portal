import base64, io, tarfile
from packagers import package_af3


def _names(b64: str) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(b64))) as t:
        return t.getnames()


def test_af3_packager_extracts_the_model_cif():
    output = {
        "status": "ok",
        "ranked_0_cif": "data_af3_job\n#\n",
        "pae_scores": {"ptm": 0.8, "iptm": 0.7},
        "stderr_tail": "warn",
    }
    names = _names(package_af3(output))
    assert "output.json" in names
    assert "af3_model.cif" in names


def test_af3_packager_handles_missing_cif():
    names = _names(package_af3({"status": "ok"}))
    assert "output.json" in names  # still produces the json, no crash
