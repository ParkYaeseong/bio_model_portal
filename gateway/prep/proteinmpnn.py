from __future__ import annotations
import base64
import re
from typing import Any
from . import structure
from .defaults import apply_defaults

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


def _parse_fixed_positions_text(text: str) -> dict[str, list[int]]:
    """Parse the UI's comma-separated chain-prefixed form, e.g. "A1,A5-8,B3",
    into the {chain: [resseq, ...]} shape the worker expects. Mirrors the
    chain-prefixed-token convention already used for RFD3's contigs/hotspots."""
    out: dict[str, list[int]] = {}
    for token in text.replace(" ", "").split(","):
        if not token:
            continue
        m = re.match(r"^([A-Za-z])(\d+)(?:-(\d+))?$", token)
        if not m:
            raise ValueError(f"invalid fixed_positions token: {token!r} (expected e.g. A1 or A5-8)")
        chain, start, end = m.group(1), int(m.group(2)), m.group(3)
        positions = range(start, int(end) + 1) if end else (start,)
        out.setdefault(chain, [])
        out[chain].extend(positions)
    return {chain: sorted(set(positions)) for chain, positions in out.items()}


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
    # Pin model defaults (temp, num_seq, batch, backbone_noise) so blanks become
    # explicit values here instead of relying on the worker's implicit defaults.
    payload = apply_defaults(payload, "proteinmpnn")

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

    # The portal UI sends fixed_positions as free text (e.g. "A1,A5-8,B3"); the
    # MCP/API path may already send the {chain: [resseq, ...]} dict directly.
    fixed_positions = out.get("fixed_positions")
    if isinstance(fixed_positions, str):
        fixed_positions = _parse_fixed_positions_text(fixed_positions) or None
        if fixed_positions:
            out["fixed_positions"] = fixed_positions
        else:
            out.pop("fixed_positions", None)

    # A <select> sends "true"/"false" strings; a naive `if value:` would treat
    # "false" as truthy, so coerce explicitly rather than trusting Python's bool().
    if isinstance(out.get("use_soluble_model"), str):
        out["use_soluble_model"] = out["use_soluble_model"].strip().lower() == "true"

    # Remap fixed_positions through the preprocess mapping unless the caller says
    # they are already in processed coordinates.
    fixed_positions = out.get("fixed_positions")
    if fixed_positions and not payload.get("fixed_positions_processed_coords"):
        out["fixed_positions"] = _remap_fixed_positions(fixed_positions, mapping)

    out["cleanup"] = payload.get("cleanup", True)

    return out
