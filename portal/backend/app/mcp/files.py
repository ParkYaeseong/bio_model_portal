"""File input contract for agent-driven runs (MCP tools / in-UI chatbot).

The portal's UI uploads real bytes via multipart; an MCP client has no such
channel, so `run_model`/`run_chain` accept a `files` list. Historically an entry
was only honored when it carried `base64`, and an entry with just a `name` was
silently dropped -- the job submitted fine and then failed minutes later inside
the gateway adapter ("no PDB content found in payload"). `name` is a *filename*,
never a path on the server, so nothing could have been read from it.

This module is the single place that turns a `files` entry into real bytes and
rejects -- loudly, before submit -- anything that carries no content. Four
sources are supported:

    {"name": "x.pdb", "base64": "..."}   raw bytes, base64-encoded
    {"name": "x.pdb", "text": "ATOM..."} text formats (PDB/CIF/FASTA/SDF) verbatim
    {"path": "x.pdb"}                    a file in the caller's server-side workspace
    {"file_id": "x.pdb"}                 a file staged earlier via the upload_file tool

`path`/`file_id` exist because pushing a multi-megabyte base64 blob through one
tool call is fragile. A client that can reach the server's filesystem writes into
its own workspace directory (see `workspace_dir`, reported by the list_files
tool) and passes a path; a remote client uploads once with upload_file -- in
chunks if needed -- and then refers to the returned file_id.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
from pathlib import Path

from ..config import get_settings

MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB per file, matching the UI upload cap

_CONTENT_KEYS = ("base64", "text", "content", "path", "file_id")


def _settings():
    return get_settings()


def workspace_dir(user) -> Path:
    """Per-user staging directory for uploaded/staged inputs.

    Per-user (not shared) so one portal account can never read another's staged
    files through a crafted `path`.
    """
    path = Path(_settings().storage_root) / "workspace" / str(user.id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def extra_roots() -> list[Path]:
    """Additional server directories a `path` entry may read from.

    Configured with MCP_FILE_ROOTS (os.pathsep-separated). Empty by default:
    without it, `path` can only reach the caller's own workspace, so a leaked
    PAT cannot be turned into an arbitrary-file read.
    """
    raw = str(getattr(_settings(), "mcp_file_roots", "") or "")
    roots: list[Path] = []
    for part in re.split(r"[:;,]", raw):
        part = part.strip()
        if not part:
            continue
        try:
            roots.append(Path(part).resolve())
        except OSError:
            continue
    return roots


def safe_name(name: str | None) -> str:
    """Filename-only, filesystem-safe form of a caller-supplied name."""
    base = Path(str(name or "").replace("\\", "/")).name
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", base).lstrip(".")
    return cleaned or "input"


def _decode_base64(blob: str, label: str) -> bytes:
    # Tool-call transport mangles long payloads with stray whitespace/newlines;
    # strip those rather than failing on an otherwise-valid upload.
    compact = re.sub(r"\s+", "", str(blob))
    try:
        return base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(
            f"{label}: 'base64' is not valid base64 ({exc}). Send the file with "
            f"'text' instead if it is a text format (PDB/CIF/FASTA/SDF), or upload "
            f"it in chunks with the upload_file tool."
        ) from exc


def resolve_workspace_path(user, raw_path: str, label: str) -> Path:
    """Resolve a caller-supplied server path inside the allowed roots."""
    ws = workspace_dir(user)
    candidate = Path(str(raw_path).strip())
    roots = [ws.resolve(), *extra_roots()]
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (ws / candidate).resolve()
        if not resolved.exists():
            # A relative path may also name a file inside a configured extra root.
            for root in extra_roots():
                alt = (root / candidate).resolve()
                if alt.exists():
                    resolved = alt
                    break
    if not any(resolved == root or root in resolved.parents for root in roots):
        allowed = ", ".join(str(r) for r in roots)
        raise ValueError(
            f"{label}: path '{raw_path}' is outside the directories this server "
            f"will read. Allowed: {allowed}. Copy the file into your workspace "
            f"directory (the list_files tool reports it) and pass a path relative "
            f"to it, or upload the bytes with upload_file."
        )
    if not resolved.exists():
        raise ValueError(
            f"{label}: no such file on the server: '{raw_path}' (looked in "
            f"{ws}). 'name' is only a filename — it is never read as a path."
        )
    if not resolved.is_file():
        raise ValueError(f"{label}: '{raw_path}' is not a regular file.")
    return resolved


def _entry_bytes(user, entry: dict, label: str) -> tuple[str, bytes]:
    """(filename, content) for one `files` entry. Raises ValueError if the entry
    carries no content."""
    present = [k for k in _CONTENT_KEYS if str(entry.get(k) or "").strip()]
    if not present:
        given = ", ".join(f"{k}={entry[k]!r}" for k in sorted(entry)) or "(empty object)"
        # Name the caller's actual workspace directory here: this is usually the
        # first error an agent sees, and telling it to `cp` the file into a real
        # path is what stops it from base64-ing a multi-megabyte structure
        # through its own context one chunk at a time.
        raise ValueError(
            f"{label}: no file content. 'name' is just a filename — the server "
            f"cannot read a local path from it. Supply the content one of these "
            f"ways:\n"
            f"  1. BEST if you can run shell on this server: "
            f"`cp <your file> {workspace_dir(user)}/` then pass "
            f'{{"path": "<file name>"}} — no encoding, no token cost.\n'
            f'  2. Small text file (PDB/CIF/FASTA/SDF): {{"text": "<file text>"}}.\n'
            f'  3. Otherwise upload once with upload_file and pass {{"file_id": ...}}.\n'
            f"Got: {given}"
        )

    name = entry.get("name")
    if "base64" in present:
        data = _decode_base64(entry["base64"], label)
    elif "text" in present or "content" in present:
        data = str(entry.get("text") or entry.get("content")).encode("utf-8")
    else:
        key = "path" if "path" in present else "file_id"
        source = resolve_workspace_path(user, str(entry[key]), label)
        data = source.read_bytes()
        name = name or source.name

    if not data:
        raise ValueError(f"{label}: file content is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(
            f"{label}: file is {len(data)} bytes, over the {MAX_FILE_BYTES}-byte limit."
        )
    return safe_name(name), data


def stage_files(user, files, tmp_dir: Path) -> list[Path]:
    """Write every `files` entry into tmp_dir and return the staged paths.

    Raises ValueError (user-facing) for any entry that cannot be turned into
    bytes — nothing is silently dropped.
    """
    tmp_dir = Path(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    staged: list[Path] = []
    used: set[str] = set()
    for index, entry in enumerate(files or [], start=1):
        label = f"files[{index - 1}]"
        if not isinstance(entry, dict):
            raise ValueError(
                f"{label}: expected an object like "
                f'{{"name": "input.pdb", "base64": "..."}}, got {type(entry).__name__}.'
            )
        name, data = _entry_bytes(user, entry, label)
        if name in used:
            stem, dot, suffix = name.partition(".")
            name = f"{stem}_{index}{dot}{suffix}"
        used.add(name)
        dest = tmp_dir / name
        dest.write_bytes(data)
        staged.append(dest)
    return staged


def save_workspace_file(user, name: str, data: bytes, *, append: bool = False) -> dict:
    """Store (or append to) a file in the caller's workspace; returns its handle."""
    filename = safe_name(name)
    dest = workspace_dir(user) / filename
    if append and dest.exists():
        existing = dest.stat().st_size
        if existing + len(data) > MAX_FILE_BYTES:
            raise ValueError(
                f"appending {len(data)} bytes would exceed the {MAX_FILE_BYTES}-byte limit."
            )
        with dest.open("ab") as handle:
            handle.write(data)
    else:
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"file is over the {MAX_FILE_BYTES}-byte limit.")
        dest.write_bytes(data)
    return describe(dest)


def describe(path: Path) -> dict:
    raw = path.read_bytes()
    return {
        "file_id": path.name,
        "name": path.name,
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def list_workspace_files(user) -> list[dict]:
    root = workspace_dir(user)
    return [describe(p) for p in sorted(root.iterdir()) if p.is_file()]
