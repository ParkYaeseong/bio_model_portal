from __future__ import annotations
import base64
from typing import Any
from . import structure
from .antibody_cdr import _call_anarcii
from .defaults import apply_defaults

_VALID_REGION_TOKENS = {
    "all", "allH", "allL",
    "FWH", "FWL", "CDRH", "CDRL",
    "FW1", "FWH1", "FWL1", "CDR1", "CDRH1", "CDRL1",
    "FW2", "FWH2", "FWL2", "CDR2", "CDRH2", "CDRL2",
    "FW3", "FWH3", "FWL3", "CDR3", "CDRH3", "CDRL3",
    "FW4", "FWH4", "FWL4",
    # AntiFold's own CLI default is "CDR1 CDR2 CDR3H" (heavy CDR3 only,
    # the loop that matters most for antigen specificity) even though the
    # published IMGT_dict only lists "CDRH3" -- accepted as documented.
    "CDR3H",
}


def _validate_regions(regions: str) -> str:
    tokens = regions.split()
    if not tokens:
        raise ValueError("'regions' must not be empty")
    bad = [t for t in tokens if t not in _VALID_REGION_TOKENS]
    if bad:
        raise ValueError(
            f"unknown AntiFold region(s) {bad!r}; expected values from "
            f"{sorted(_VALID_REGION_TOKENS)}"
        )
    return regions


def _imgt_positions_for_chain(sequence: str) -> dict[int, tuple[int, str]] | None:
    """{sequence_index (0-based): (imgt_position, insertion_code)} for every
    residue ANARCII placed, or None if it couldn't number this chain at all
    (not an antibody domain)."""
    try:
        domains = _call_anarcii(sequence)
    except Exception:
        return None
    for domain in domains.values():
        if domain.get("error"):
            continue
        positions: dict[int, tuple[int, str]] = {}
        seq_idx = int(domain.get("query_start") or 0)
        for entry in domain.get("numbering") or []:
            (imgt_position, icode), amino_acid = entry
            if amino_acid == "-":
                continue
            positions[seq_idx] = (int(imgt_position), str(icode or "").strip())
            seq_idx += 1
        if positions:
            return positions
    return None


def _renumber_pdb_to_imgt(pdb_text: str, chain_ids: list[str]) -> str:
    """Rewrite ATOM/HETATM residue numbers (+ insertion code) for the given
    chains to IMGT positions via ANARCII.

    AntiFold requires its input PDB to already be IMGT-numbered (it reads
    positions 1-128 directly off the structure); this does that renumbering
    here instead of depending on AntiFold's own vendored ANARCI/ImmunoPDB.py
    tool, since ANARCII is already deployed and used for this exact purpose
    by the ProteinMPNN antibody-CDR-masking mode. Chains ANARCII can't place,
    and chains not in `chain_ids` (e.g. an antigen chain), are left untouched.
    """
    from bio.pdb import sequence_by_chain

    sequences = sequence_by_chain(pdb_text, chains=chain_ids)
    imgt_by_chain: dict[str, dict[int, tuple[int, str]]] = {}
    for chain_id in chain_ids:
        sequence = sequences.get(chain_id)
        if not sequence:
            continue
        positions = _imgt_positions_for_chain(sequence)
        if positions:
            imgt_by_chain[chain_id] = positions

    out_lines: list[str] = []
    chain_residue_index: dict[str, int] = {}
    last_residue_key: dict[str, tuple[str, str]] = {}
    for line in pdb_text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        newline = line[len(stripped):]
        record = stripped[:6].strip().upper() if len(stripped) >= 6 else ""
        if record not in {"ATOM", "HETATM"} or len(stripped) < 27:
            out_lines.append(line)
            continue
        chain_id = stripped[21:22].strip() or "_"
        positions = imgt_by_chain.get(chain_id)
        if positions is None:
            out_lines.append(line)
            continue
        key = (stripped[22:26], stripped[26:27])
        if last_residue_key.get(chain_id) != key:
            chain_residue_index[chain_id] = chain_residue_index.get(chain_id, -1) + 1
            last_residue_key[chain_id] = key
        mapped = positions.get(chain_residue_index[chain_id])
        if mapped is None:
            out_lines.append(line)
            continue
        imgt_pos, icode = mapped
        new_body = f"{stripped[:22]}{imgt_pos:>4d}{(icode or ' ')[:1]}{stripped[27:]}"
        out_lines.append(new_body + newline)
    return "".join(out_lines)


def build_input(payload: dict) -> dict:
    """Build the AntiFold worker payload from a portal payload.

    The uploaded structure does not need to be pre-numbered: the antibody
    chain(s) are renumbered to IMGT here (see `_renumber_pdb_to_imgt`)
    before being sent to the worker, which is what AntiFold itself requires.
    """
    payload = apply_defaults(payload, "antifold")
    out: dict[str, Any] = {}

    pdb_b64 = payload.get("pdb_base64")
    pdb_text = payload.get("pdb_text") or payload.get("pdb_content")
    if pdb_b64 and not pdb_text:
        pdb_text = base64.b64decode(pdb_b64).decode("utf-8", errors="replace")
    if not pdb_text:
        pdb_text = structure.extract_pdb_text(payload)
    if not pdb_text:
        raise ValueError("AntiFold: no PDB content found in payload")

    nanobody_chain = str(payload.get("nanobody_chain") or "").strip()
    if nanobody_chain:
        antibody_chains = [nanobody_chain]
        out["nanobody_chain"] = nanobody_chain
    else:
        heavy_chain = str(payload.get("heavy_chain") or "H").strip() or "H"
        light_chain = str(payload.get("light_chain") or "L").strip() or "L"
        antibody_chains = [heavy_chain, light_chain]
        out["heavy_chain"] = heavy_chain
        out["light_chain"] = light_chain

    pdb_text = _renumber_pdb_to_imgt(pdb_text, antibody_chains)
    out["pdb_base64"] = base64.b64encode(pdb_text.encode("utf-8")).decode("ascii")
    out["pdb_name"] = payload.get("pdb_name") or "input"

    antigen_chain = str(payload.get("antigen_chain") or "").strip()
    if antigen_chain:
        out["antigen_chain"] = antigen_chain

    out["regions"] = _validate_regions(str(payload.get("regions") or "CDR1 CDR2 CDR3H").strip())

    if payload.get("num_seq_per_target") not in (None, ""):
        out["num_seq_per_target"] = int(payload["num_seq_per_target"])
    if payload.get("sampling_temp") not in (None, ""):
        out["sampling_temp"] = str(payload["sampling_temp"])
    if payload.get("seed") not in (None, ""):
        out["seed"] = int(payload["seed"])

    if isinstance(payload.get("custom_chain_mode"), str):
        out["custom_chain_mode"] = payload["custom_chain_mode"].strip().lower() == "true"
    elif payload.get("custom_chain_mode") is not None:
        out["custom_chain_mode"] = bool(payload["custom_chain_mode"])

    if isinstance(payload.get("esm_if1_mode"), str):
        out["esm_if1_mode"] = payload["esm_if1_mode"].strip().lower() == "true"
    elif payload.get("esm_if1_mode") is not None:
        out["esm_if1_mode"] = bool(payload["esm_if1_mode"])

    return out
