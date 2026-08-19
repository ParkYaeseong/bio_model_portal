import base64, io, tarfile
from packagers import package_rfd3


def _names(b64: str) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(b64))) as t:
        return t.getnames()


def test_rfd3_packager_extracts_design_pdbs():
    output = {
        "status": "ok",
        "selected": {"id": "design_a", "pdb": "ATOM      1  N   ILE A   1\n"},
        "designs": [
            {"id": "design_a", "pdb": "ATOM      1  N   ILE A   1\n"},
            {"id": "design_b", "pdb": "ATOM      1  N   ALA A   1\n"},
        ],
        "stderr_tail": "warn",
    }
    names = _names(package_rfd3(output))
    assert "output.json" in names
    assert "design_a.pdb" in names and "design_b.pdb" in names
    assert "selected.pdb" in names


def test_rfd3_packager_handles_missing_designs():
    names = _names(package_rfd3({"status": "ok"}))
    assert "output.json" in names  # still produces the json, no crash
