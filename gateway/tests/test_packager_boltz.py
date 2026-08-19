import base64, io, tarfile
from packagers import package_boltz


def _names(b64: str) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(b64))) as t:
        return t.getnames()


def test_boltz_packager_extracts_the_model_pdb():
    output = {
        "status": "ok",
        "ranked_0_pdb": "ATOM      1  N   MET A   1\nEND\n",
        "scores": {"ptm": 0.9, "complex_plddt": 0.87},
        "stderr_tail": "warn",
    }
    names = _names(package_boltz(output))
    assert "output.json" in names
    assert "boltz_model_0.pdb" in names


def test_boltz_packager_handles_missing_pdb():
    names = _names(package_boltz({"status": "ok"}))
    assert "output.json" in names  # still produces the json, no crash
