from __future__ import annotations
import base64, io, tarfile
from bio import pdb

def extract_pdb_text(payload: dict) -> str | None:
    """Return PDB/mmCIF text from inline keys or payload.input_archive (tar.gz base64)."""
    for key in ("pdb_content", "input_pdb_content"):
        if isinstance(payload.get(key), str) and payload[key].strip():
            return payload[key]
    archive = payload.get("input_archive")
    if isinstance(archive, dict) and archive.get("base64"):
        raw = base64.b64decode(archive["base64"])
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
            members = sorted((m for m in tar.getmembers() if m.isfile()), key=lambda m: m.name)
            for m in members:
                if m.name.lower().endswith((".pdb", ".cif", ".mmcif")):
                    f = tar.extractfile(m)
                    if f:
                        return f.read().decode("utf-8", errors="replace")
    return None

_FASTA_SUFFIXES = (".fasta", ".fa", ".faa", ".fna")

def extract_fasta_text(payload: dict) -> str | None:
    """Return FASTA text from payload.input_archive (tar.gz base64), if the
    upload carries a FASTA file (deterministic: first match by sorted name)."""
    archive = payload.get("input_archive")
    if not (isinstance(archive, dict) and archive.get("base64")):
        return None
    raw = base64.b64decode(archive["base64"])
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        members = sorted((m for m in tar.getmembers() if m.isfile()), key=lambda m: m.name)
        for m in members:
            if m.name.lower().endswith(_FASTA_SUFFIXES):
                f = tar.extractfile(m)
                if f:
                    return f.read().decode("utf-8", errors="replace")
    return None

_LIGAND_SUFFIXES = (".sdf", ".mol")

def extract_ligand_text(payload: dict) -> str | None:
    """Return SDF/MOL text from payload.input_archive, if the upload carries a
    ligand file. The UI's DiffDock form builds a worker-ready payload itself, so
    an archive-delivered ligand only happens on the MCP/chat path -- where it
    used to be dropped ("DiffDock requires ligand_smiles or ligand_sdf") even
    though the user had attached the SDF."""
    archive = payload.get("input_archive")
    if not (isinstance(archive, dict) and archive.get("base64")):
        return None
    raw = base64.b64decode(archive["base64"])
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        members = sorted((m for m in tar.getmembers() if m.isfile()), key=lambda m: m.name)
        for m in members:
            if m.name.lower().endswith(_LIGAND_SUFFIXES):
                f = tar.extractfile(m)
                if f:
                    return f.read().decode("utf-8", errors="replace")
    return None

def preprocess(pdb_text: str, chains: list[str] | None = None):
    """Normalize (mmcif->pdb, first model) then strip non-positive resseq + renumber from 1.
    Returns (clean_pdb_text, mapping)."""
    normalized = pdb.normalize_structure_text(pdb_text)
    return pdb.preprocess_pdb(normalized, chains=chains,
                             strip_nonpositive_resseq=True, renumber_resseq_from_1=True)
