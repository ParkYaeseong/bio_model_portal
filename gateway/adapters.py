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
    from prep import rosetta
    return rosetta.build_input(payload)


def adapter_proteinmpnn(payload: dict) -> dict:
    from prep import proteinmpnn
    return proteinmpnn.build_input(payload)


def adapter_rfdiffusion(payload: dict) -> dict:
    from prep import rfd3
    return rfd3.build_input(payload)


def adapter_diffdock(payload: dict) -> dict:
    from prep import diffdock
    return diffdock.build_input(payload)


def adapter_mmseqs(payload: dict) -> dict:
    from prep import mmseqs
    return mmseqs.build_input(payload)


def adapter_bioemu(payload: dict) -> dict:
    from prep import sequence
    return sequence.build_bioemu_input(payload)


def adapter_colabfold(payload: dict) -> dict:
    from prep import sequence
    return sequence.build_folding_input(payload, model="ColabFold")


def adapter_esmfold(payload: dict) -> dict:
    from prep import sequence
    return sequence.build_folding_input(payload, model="ESMFold")


def adapter_alphafold(payload: dict) -> dict:
    from prep import sequence
    return sequence.build_folding_input(payload, model="AlphaFold2")


def adapter_passthrough(payload: dict) -> dict:
    out = dict(payload)
    out.pop("input_archive", None)
    return out


ADAPTERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "rosetta_relax": adapter_rosetta_relax,
    "proteinmpnn": adapter_proteinmpnn,
    "rfdiffusion": adapter_rfdiffusion,
    "diffdock": adapter_diffdock,
    "mmseqs": adapter_mmseqs,
    "bioemu": adapter_bioemu,
    "colabfold": adapter_colabfold,
    "esmfold": adapter_esmfold,
    "alphafold": adapter_alphafold,
    "passthrough": adapter_passthrough,
}


def adapt(name: str | None, payload: dict[str, Any]) -> dict[str, Any]:
    if not name:
        return payload
    fn = ADAPTERS.get(name)
    if fn is None:
        return payload
    return fn(payload)
