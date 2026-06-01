from __future__ import annotations

import base64
import io
import json
import tarfile
from typing import Any, Callable


def _add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def _add_text(tar: tarfile.TarFile, name: str, text: str | None) -> None:
    if not text:
        return
    _add(tar, name, text.encode("utf-8"))


def _add_raw_json(tar: tarfile.TarFile, output: dict) -> None:
    raw = json.dumps(output, indent=2, default=str, ensure_ascii=False).encode("utf-8")
    _add(tar, "output.json", raw)


def _add_streams(tar: tarfile.TarFile, output: dict) -> None:
    _add_text(tar, "stdout.log", output.get("stdout_tail") or output.get("stdout"))
    _add_text(tar, "stderr.log", output.get("stderr_tail") or output.get("stderr"))


def _add_sample_pdbs(tar: tarfile.TarFile, output: dict) -> None:
    for idx, entry in enumerate(output.get("sample_pdbs") or []):
        pdb_text = entry.get("pdb") if isinstance(entry, dict) else None
        if not pdb_text:
            continue
        sample_id = (entry.get("id") if isinstance(entry, dict) else None) or f"sample_{idx:03d}"
        _add_text(tar, f"{sample_id}.pdb", pdb_text)
    _add_text(tar, "topology.pdb", output.get("topology_pdb"))


def _build(callback: Callable[[tarfile.TarFile], None]) -> str:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        callback(tar)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def package_bioemu(output: dict) -> str:
    def build(tar: tarfile.TarFile) -> None:
        _add_raw_json(tar, output)
        _add_sample_pdbs(tar, output)
        _add_streams(tar, output)

    return _build(build)


def package_esmfold(output: dict) -> str:
    def build(tar: tarfile.TarFile) -> None:
        _add_raw_json(tar, output)
        pdb = output.get("pdb") or output.get("pdb_text")
        if pdb:
            _add_text(tar, "esmfold.pdb", pdb)
        _add_sample_pdbs(tar, output)
        _add_streams(tar, output)

    return _build(build)


def package_esmfold2(output: dict) -> str:
    def build(tar: tarfile.TarFile) -> None:
        _add_raw_json(tar, output)
        pdb = output.get("pdb") or output.get("pdb_text")
        if pdb:
            _add_text(tar, "esmfold2.pdb", pdb)
        for entry in output.get("structures") or []:
            if isinstance(entry, dict) and entry.get("pdb"):
                seq_id = str(entry.get("id") or "structure")
                _add_text(tar, f"{seq_id}.pdb", entry["pdb"])
        _add_streams(tar, output)

    return _build(build)


def package_colabfold(output: dict) -> str:
    def build(tar: tarfile.TarFile) -> None:
        _add_raw_json(tar, output)
        for key, fname in (
            ("ranked_0_pdb", "colabfold_ranked_0.pdb"),
            ("ranked_1_pdb", "colabfold_ranked_1.pdb"),
            ("ranked_2_pdb", "colabfold_ranked_2.pdb"),
            ("pdb", "colabfold.pdb"),
            ("pdb_text", "colabfold.pdb"),
        ):
            value = output.get(key)
            if isinstance(value, str) and value.strip():
                _add_text(tar, fname, value)
        for entry in output.get("files") or []:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name") or entry.get("filename")
            content = entry.get("content") or entry.get("text") or entry.get("pdb")
            if name and isinstance(content, str) and content.strip():
                _add_text(tar, str(name), content)
        _add_streams(tar, output)

    return _build(build)


def package_proteinmpnn(output: dict) -> str:
    def build(tar: tarfile.TarFile) -> None:
        _add_raw_json(tar, output)
        for idx, entry in enumerate(output.get("sequences") or []):
            text = entry.get("fasta") or entry.get("sequence") if isinstance(entry, dict) else None
            if text:
                _add_text(tar, f"design_{idx:03d}.fasta", str(text))
        _add_streams(tar, output)

    return _build(build)


_GENERIC_PDB_KEYS = (
    "pdb",
    "pdb_text",
    "pdb_content",
    "ranked_0_pdb",
    "best_pdb",
    "relaxed_pdb",
    "relaxed_pdb_content",
    "topology_pdb",
)


def package_generic(output: dict) -> str:
    def build(tar: tarfile.TarFile) -> None:
        _add_raw_json(tar, output)
        for key in _GENERIC_PDB_KEYS:
            value = output.get(key)
            if isinstance(value, str) and value.strip():
                _add_text(tar, f"{key}.pdb", value)
        _add_sample_pdbs(tar, output)
        _add_streams(tar, output)

    return _build(build)


PACKAGERS: dict[str, Callable[[dict], str]] = {
    "bioemu": package_bioemu,
    "esmfold": package_esmfold,
    "esmfold2": package_esmfold2,
    "colabfold": package_colabfold,
    "proteinmpnn": package_proteinmpnn,
    "generic": package_generic,
}


def package(name: str, output: dict[str, Any]) -> str:
    fn = PACKAGERS.get(name) or PACKAGERS["generic"]
    return fn(output)
