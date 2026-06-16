from __future__ import annotations
import base64
from typing import Any
from . import structure

# Keys passed through to the worker unchanged when present in the payload.
_PASSTHROUGH = (
    "pdb_path_chains",
    "fixed_positions",
    "use_soluble_model",
    "model_name",
    "num_seq_per_target",
    "batch_size",
    "sampling_temp",
    "seed",
    "backbone_noise",
)


def _chains_list(payload: dict) -> list[str] | None:
    chains = payload.get("chains") or payload.get("pdb_path_chains")
    if chains is None:
        return None
    if isinstance(chains, str):
        # Accept "A B" / "A,B" / "AB"-ish strings.
        toks = chains.replace(",", " ").split()
        return toks or None
    if isinstance(chains, (list, tuple)):
        return [str(c) for c in chains] or None
    return None


def _remap_fixed_positions(fixed_positions: dict, mapping: dict) -> dict:
    """Remap each chain's 1-based residue indices from original to processed resseq.

    fixed_positions is {chain: [resseq, ...]} in ORIGINAL numbering. mapping maps
    original_resseq -> processed_resseq per chain. Unknown residues pass through
    unchanged so we never silently drop a position.
    """
    orig2proc = {
        ch: {e["original_resseq"]: e["processed_resseq"] for e in entries}
        for ch, entries in mapping.items()
    }
    out: dict[str, list[int]] = {}
    for ch, positions in fixed_positions.items():
        m = orig2proc.get(ch, {})
        out[ch] = [int(m.get(int(p), p)) for p in (positions or [])]
    return out


def build_input(payload: dict) -> dict:
    """Build the ProteinMPNN worker payload from a portal payload.

    Preprocesses the PDB (strip non-positive resseq, renumber from 1) so weird
    numbering / HETATM is sanitized before reaching the worker.
    """
    out: dict[str, Any] = {}

    chains = _chains_list(payload)

    # Respect an already-supplied PDB; otherwise extract from the archive/inline.
    pdb_b64 = payload.get("pdb_base64")
    pdb_text = payload.get("pdb_text") or payload.get("pdb_content")
    if pdb_b64 and not pdb_text:
        pdb_text = base64.b64decode(pdb_b64).decode("utf-8", errors="replace")
    if not pdb_text:
        pdb_text = structure.extract_pdb_text(payload)
    if not pdb_text:
        raise ValueError("ProteinMPNN: no PDB content found in payload")

    clean, mapping = structure.preprocess(pdb_text, chains=chains)
    out["pdb_base64"] = base64.b64encode(clean.encode("utf-8")).decode("ascii")
    out["pdb_name"] = payload.get("pdb_name") or "input"

    for key in _PASSTHROUGH:
        if key in payload and payload[key] is not None:
            out[key] = payload[key]

    # Remap fixed_positions through the preprocess mapping unless the caller says
    # they are already in processed coordinates.
    fixed_positions = out.get("fixed_positions")
    if fixed_positions and not payload.get("fixed_positions_processed_coords"):
        out["fixed_positions"] = _remap_fixed_positions(fixed_positions, mapping)

    out["cleanup"] = payload.get("cleanup", True)

    return out
