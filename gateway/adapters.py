from __future__ import annotations

import base64
import io
import tarfile
from typing import Any, Callable


def _extract_archive(payload: dict) -> dict[str, bytes]:
    """Pull files out of portal's input_archive (tar.gz, base64-encoded)."""
    archive = payload.get("input_archive")
    if not isinstance(archive, dict):
        return {}
    blob = archive.get("base64")
    if not blob:
        return {}
    raw = base64.b64decode(blob)
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            f = tar.extractfile(member)
            if f is None:
                continue
            files[member.name] = f.read()
    return files


def _pick_pdb(files: dict[str, bytes]) -> tuple[str, bytes] | None:
    pdbs = [(name, data) for name, data in files.items() if name.lower().endswith(".pdb")]
    if not pdbs:
        return None
    pdbs.sort()
    return pdbs[0]


def adapter_rosetta_relax(payload: dict) -> dict:
    if payload.get("pdb_content") or payload.get("input_pdb_content"):
        return payload
    files = _extract_archive(payload)
    pdb = _pick_pdb(files)
    if not pdb:
        return payload
    name, data = pdb
    out = dict(payload)
    out["pdb_content"] = data.decode("utf-8", errors="replace")
    out.setdefault("target_id", name.rsplit("/", 1)[-1].removesuffix(".pdb"))
    out.pop("input_archive", None)
    return out


def adapter_proteinmpnn(payload: dict) -> dict:
    if payload.get("pdb_base64") or payload.get("pdb_text") or payload.get("pdb_content"):
        return payload
    files = _extract_archive(payload)
    pdb = _pick_pdb(files)
    if not pdb:
        return payload
    name, data = pdb
    out = dict(payload)
    out["pdb_base64"] = base64.b64encode(data).decode("ascii")
    out.setdefault("pdb_name", name.rsplit("/", 1)[-1].removesuffix(".pdb"))
    out.pop("input_archive", None)
    return out


def adapter_rfdiffusion(payload: dict) -> dict:
    out = dict(payload)
    if not (isinstance(out.get("input_files"), dict) and out["input_files"]):
        pdb = _pick_pdb(_extract_archive(out))
        if pdb:
            _, data = pdb
            out["input_files"] = {"input.pdb": data.decode("utf-8", errors="replace")}
    has_input_pdb = isinstance(out.get("input_files"), dict) and "input.pdb" in out["input_files"]
    if not isinstance(out.get("inputs"), dict):
        spec: dict[str, Any] = {}
        contigs = out.pop("contigs", None) or out.pop("contig", None)
        length = out.pop("length", None)
        if has_input_pdb:
            spec["input"] = "input.pdb"
            if contigs:
                spec["contig"] = contigs
            if length:
                spec["length"] = str(length)
        else:
            # Unconditional generation: RFD3 expects `length`, not `contig`.
            if length:
                spec["length"] = str(length)
            elif contigs:
                spec["length"] = str(contigs)
        hotspots = out.pop("hotspots", None) or out.pop("hotspot_res", None)
        if hotspots:
            spec["hotspots"] = hotspots
        if spec:
            out["inputs"] = {"spec-1": spec}
    out.pop("input_archive", None)
    return out


def adapter_mmseqs(payload: dict) -> dict:
    out = dict(payload)
    if out.get("query_fasta"):
        out.pop("input_archive", None)
        return out
    sequence = str(out.pop("sequence", "") or "").strip()
    if sequence:
        if not sequence.startswith(">"):
            sequence = f">query\n{sequence}\n"
        out["query_fasta"] = sequence
    else:
        files = _extract_archive(out)
        for name, data in files.items():
            if name.lower().endswith((".fasta", ".fa", ".faa", ".fna")):
                out["query_fasta"] = data.decode("utf-8", errors="replace")
                break
    out.setdefault("task", "search")
    out.setdefault("target_db", "uniref90")
    out.pop("input_archive", None)
    return out


def adapter_passthrough(payload: dict) -> dict:
    out = dict(payload)
    out.pop("input_archive", None)
    return out


ADAPTERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "rosetta_relax": adapter_rosetta_relax,
    "proteinmpnn": adapter_proteinmpnn,
    "rfdiffusion": adapter_rfdiffusion,
    "mmseqs": adapter_mmseqs,
    "passthrough": adapter_passthrough,
}


def adapt(name: str | None, payload: dict[str, Any]) -> dict[str, Any]:
    if not name:
        return payload
    fn = ADAPTERS.get(name)
    if fn is None:
        return payload
    return fn(payload)
