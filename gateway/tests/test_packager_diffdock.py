import base64, io, tarfile, zipfile
from packagers import package_diffdock


def _names(b64: str) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(b64))) as t:
        return t.getnames()


def _make_zip(files: dict[str, bytes]) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return base64.b64encode(buf.getvalue()).decode()


def test_diffdock_packager_extracts_poses_from_zip():
    sdf = b"\n  pose\n\n  1  0  0  0  0  0\nM  END\n$$$$\n"
    output = {
        "returncode": 0,
        "out_dir_zip_b64": _make_zip({"t/rank1.sdf": sdf, "t/rank2.sdf": sdf}),
        "stdout": "ok",
    }
    names = _names(package_diffdock(output))
    assert "output.json" in names
    assert "t_rank1.sdf" in names and "t_rank2.sdf" in names


def test_diffdock_packager_handles_empty_zip():
    output = {"returncode": 0, "out_dir_zip_b64": _make_zip({})}
    names = _names(package_diffdock(output))
    assert "output.json" in names  # no crash, json still present


def test_diffdock_packager_handles_missing_zip():
    names = _names(package_diffdock({"returncode": 1, "stderr": "boom"}))
    assert "output.json" in names
