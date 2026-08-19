"""The `files` input contract: nothing is accepted without real content."""
import base64
import uuid

import pytest

from app import models
from app.database import SessionLocal
from app.mcp import files as mcp_files


def _user(db):
    u = models.User(username=f"t_{uuid.uuid4().hex[:8]}", password_hash="x")
    db.add(u); db.commit(); db.refresh(u)
    return u


def test_name_only_entry_is_rejected_with_an_actionable_message(tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        with pytest.raises(ValueError) as exc:
            mcp_files.stage_files(u, [{"name": "data/ab.pdb"}], tmp_path)
        message = str(exc.value)
        assert "no file content" in message
        assert "not a path" in message or "cannot read a local path" in message
        # names every accepted way to supply content
        for key in ("base64", "text", "path", "file_id"):
            assert key in message


def test_text_entry_is_written_verbatim(tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        staged = mcp_files.stage_files(u, [{"name": "ab.pdb", "text": "ATOM      1  N\n"}], tmp_path)
        assert [p.name for p in staged] == ["ab.pdb"]
        assert staged[0].read_text() == "ATOM      1  N\n"


def test_base64_entry_tolerates_wrapped_whitespace(tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        blob = base64.b64encode(b"ATOM\n" * 40).decode()
        wrapped = "\n".join(blob[i:i + 16] for i in range(0, len(blob), 16))
        staged = mcp_files.stage_files(u, [{"name": "x.pdb", "base64": wrapped}], tmp_path)
        assert staged[0].read_bytes() == b"ATOM\n" * 40


def test_invalid_base64_is_reported_as_such(tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        with pytest.raises(ValueError, match="not valid base64"):
            mcp_files.stage_files(u, [{"name": "x.pdb", "base64": "not base64 !!"}], tmp_path)


def test_empty_content_is_rejected(tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        with pytest.raises(ValueError, match="no file content"):
            mcp_files.stage_files(u, [{"name": "x.pdb", "text": "   "}], tmp_path)


def test_uploaded_file_can_be_referenced_by_file_id(tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        stored = mcp_files.save_workspace_file(u, "ab.pdb", b"ATOM\n")
        assert stored["file_id"] == "ab.pdb" and stored["size_bytes"] == 5
        staged = mcp_files.stage_files(u, [{"file_id": "ab.pdb"}], tmp_path)
        assert staged[0].read_bytes() == b"ATOM\n"
        assert staged[0].name == "ab.pdb"


def test_upload_append_builds_a_file_in_chunks(tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        mcp_files.save_workspace_file(u, "big.pdb", b"PART1")
        stored = mcp_files.save_workspace_file(u, "big.pdb", b"PART2", append=True)
        assert stored["size_bytes"] == 10
        staged = mcp_files.stage_files(u, [{"file_id": "big.pdb"}], tmp_path)
        assert staged[0].read_bytes() == b"PART1PART2"


def test_path_outside_the_workspace_is_refused(tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        outside = tmp_path / "secret.pdb"
        outside.write_text("ATOM\n")
        with pytest.raises(ValueError, match="outside the directories"):
            mcp_files.stage_files(u, [{"path": str(outside)}], tmp_path)
        with pytest.raises(ValueError, match="outside the directories"):
            mcp_files.stage_files(u, [{"path": "../../etc/passwd"}], tmp_path)


def test_path_cannot_read_another_users_workspace(tmp_path):
    with SessionLocal() as db:
        u1, u2 = _user(db), _user(db)
        mcp_files.save_workspace_file(u1, "private.pdb", b"ATOM\n")
        other = mcp_files.workspace_dir(u1) / "private.pdb"
        with pytest.raises(ValueError, match="outside the directories"):
            mcp_files.stage_files(u2, [{"path": str(other)}], tmp_path)


def test_missing_workspace_path_says_name_is_not_a_path(tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        with pytest.raises(ValueError, match="no such file"):
            mcp_files.stage_files(u, [{"path": "nope.pdb"}], tmp_path)


def test_extra_root_can_be_allowlisted(tmp_path, monkeypatch):
    with SessionLocal() as db:
        u = _user(db)
        shared = tmp_path / "shared"
        shared.mkdir()
        (shared / "ref.pdb").write_text("ATOM\n")
        monkeypatch.setattr(mcp_files, "extra_roots", lambda: [shared.resolve()])
        staged = mcp_files.stage_files(u, [{"path": "ref.pdb"}], tmp_path / "stage")
        assert staged[0].read_text() == "ATOM\n"


def test_oversize_file_is_rejected(tmp_path, monkeypatch):
    with SessionLocal() as db:
        u = _user(db)
        monkeypatch.setattr(mcp_files, "MAX_FILE_BYTES", 8)
        with pytest.raises(ValueError, match="over the"):
            mcp_files.stage_files(u, [{"name": "x.pdb", "text": "way too long"}], tmp_path)


def test_duplicate_names_do_not_overwrite_each_other(tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        staged = mcp_files.stage_files(
            u, [{"name": "x.pdb", "text": "A"}, {"name": "x.pdb", "text": "B"}], tmp_path)
        assert len(staged) == 2 and staged[0] != staged[1]
        assert {p.read_text() for p in staged} == {"A", "B"}


def test_non_object_entry_is_rejected(tmp_path):
    with SessionLocal() as db:
        u = _user(db)
        with pytest.raises(ValueError, match="expected an object"):
            mcp_files.stage_files(u, ["ab.pdb"], tmp_path)
