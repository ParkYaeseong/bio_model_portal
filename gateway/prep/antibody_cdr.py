from __future__ import annotations

import os
import re
from typing import Any

import httpx

# Same host as every other bop worker (see endpoints.yaml); ANARCII isn't
# registered as a portal pipeline in its own right -- this is a fast,
# CPU-only sub-call made synchronously from within build_input(), which
# itself already runs off the request thread (see JobManager.submit()).
ANARCII_URL = os.getenv("ANARCII_URL", "http://211.188.35.221:18109").rstrip("/")

# IMGT (Lefranc) CDR boundaries.
_CDR1 = range(27, 39)
_CDR2 = range(56, 66)
_CDR3 = range(105, 118)
# VHH hallmark positions: never mutate these even with framework included --
# they define the single-domain (camelid) fold and are not designable.
_VHH_HALLMARK_POSITIONS = {37, 44, 45, 47}


def _region_for_position(chain_type: str, imgt_position: int) -> str:
    suffix = "H" if chain_type == "H" else "L"
    if imgt_position in _CDR1:
        return f"CDR-{suffix}1"
    if imgt_position in _CDR2:
        return f"CDR-{suffix}2"
    if imgt_position in _CDR3:
        return f"CDR-{suffix}3"
    return f"FR-{suffix}"


def _call_anarcii(sequence: str) -> dict[str, Any]:
    with httpx.Client(timeout=60) as client:
        response = client.post(
            f"{ANARCII_URL}/run",
            json={"input": {"task": "number", "sequences": {"query": sequence}, "scfv": True}},
        )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"ANARCII numbering failed: {data}")
    output = data.get("output")
    if not isinstance(output, dict):
        raise RuntimeError(f"ANARCII returned no domains: {data}")
    return output


def _domain_mutable_positions(domain: dict[str, Any], *, include_framework: bool) -> dict[int, bool]:
    """{sequence_index (0-based, in the ORIGINAL query): mutable?} for every
    residue ANARCII actually placed (alignment gaps '-' are skipped, so gaps
    never shift the mapping back to the real sequence)."""
    chain_type = str(domain.get("chain_type") or "H")
    query_start = int(domain.get("query_start") or 0)
    out: dict[int, bool] = {}
    seq_idx = query_start
    for entry in domain.get("numbering") or []:
        (imgt_position, _insertion_code), amino_acid = entry
        if amino_acid == "-":
            continue
        imgt_position = int(imgt_position)
        region = _region_for_position(chain_type, imgt_position)
        is_cdr = region.startswith("CDR-")
        is_fr = region.startswith("FR-")
        mutable = is_cdr or (include_framework and is_fr and imgt_position not in _VHH_HALLMARK_POSITIONS)
        out[seq_idx] = mutable
        seq_idx += 1
    return out


def _parse_residue_tokens(text: str) -> set[tuple[str, int]]:
    """Parse 'A1,A5-8,B3' into {(chain, resseq), ...} -- same convention as
    the plain-text fixed_positions field elsewhere in this module."""
    out: set[tuple[str, int]] = set()
    for token in (text or "").replace(" ", "").split(","):
        if not token:
            continue
        match = re.match(r"^([A-Za-z])(\d+)(?:-(\d+))?$", token)
        if not match:
            raise ValueError(f"invalid residue token: {token!r} (expected e.g. A1 or A5-8)")
        chain, start, end = match.group(1), int(match.group(2)), match.group(3)
        for pos in range(start, int(end) + 1 if end else start + 1):
            out.add((chain, pos))
    return out


def compute_antibody_fixed_positions(
    pdb_text: str,
    *,
    chains: list[str] | None = None,
    include_framework: bool = False,
    mutable_include: str | None = None,
    mutable_exclude: str | None = None,
) -> dict[str, list[int]]:
    """CDR-focused ProteinMPNN fixed_positions: only IMGT CDR loops (plus,
    optionally, framework residues -- except the VHH hallmark positions and
    cysteines, which are never mutable) are left open; everything else on an
    antibody chain is fixed.

    This is a simplification of antigen_pipeline's full antibody-design
    analysis: without a bound antigen there is nothing to compute contact
    stats or surface exposure against, so this only applies the IMGT
    region + cysteine/hallmark safety nets, not contact- or exposure-based
    filtering.

    A chain ANARCII cannot number at all (not an antibody domain -- e.g. a
    tag, linker-only fragment, or a chain the caller didn't mean to include)
    is fixed in its entirety rather than left fully open, since "not an
    antibody" should never silently mean "redesign freely".
    """
    from bio import pdb as _pdb

    residues_by_chain = _pdb.residues_by_chain(pdb_text)
    sequences = _pdb.sequence_by_chain(pdb_text, chains=chains)
    if not sequences:
        raise ValueError("no chain sequence found in the uploaded structure")

    include_tokens = _parse_residue_tokens(mutable_include) if mutable_include else set()
    exclude_tokens = _parse_residue_tokens(mutable_exclude) if mutable_exclude else set()

    fixed_positions: dict[str, list[int]] = {}
    for chain_id, sequence in sequences.items():
        chain_residues = residues_by_chain.get(chain_id) or []
        mutable_resseqs: set[int] = set()

        try:
            domains = _call_anarcii(sequence)
        except Exception:
            domains = {}

        for domain in domains.values():
            if domain.get("error"):
                continue
            for seq_idx, mutable in _domain_mutable_positions(domain, include_framework=include_framework).items():
                if not mutable or seq_idx >= len(chain_residues):
                    continue
                residue = chain_residues[seq_idx]
                if residue.resname.upper() == "CYS":
                    continue
                mutable_resseqs.add(residue.resseq)

        for chain, pos in include_tokens:
            if chain == chain_id:
                mutable_resseqs.add(pos)
        for chain, pos in exclude_tokens:
            if chain == chain_id:
                mutable_resseqs.discard(pos)

        fixed_positions[chain_id] = sorted(r.resseq for r in chain_residues if r.resseq not in mutable_resseqs)

    return fixed_positions
