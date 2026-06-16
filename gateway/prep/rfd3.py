from __future__ import annotations
import re
from typing import Any
from . import structure

def _normalize_contig_token(value: str) -> str:
    return str(value or "").replace(":", "")  # "A:1" -> "A1" (mirror protein_pipeline)

def _remap_contig(contig: str, mapping: dict, *, processed_coords: bool) -> str:
    """Remap a custom contig from original PDB numbering to preprocessed numbering.
    Dropdown/recommended contigs are already in processed coords -> normalize only.
    Tracks the active chain so bare range endpoints (e.g. the '210' in 'A201-210')
    remap using the same chain as the range start.

    Pattern: chain-prefixed tokens use ([A-Za-z])(-?\\d+) so negative resids like
    'B-2' work. Bare endpoints (digits only, no chain letter) use (\\d+) — they
    cannot be negative because '-' before them is the range separator, not a sign.
    """
    contig = _normalize_contig_token(contig)
    if processed_coords or not mapping:
        return contig
    orig2proc = {ch: {e["original_resseq"]: e["processed_resseq"] for e in entries}
                 for ch, entries in mapping.items()}
    state = {"chain": None}
    def repl(m):
        # Alternative 1 matched: chain-prefixed token e.g. "A201" or "B-2"
        if m.group(1) is not None:
            ch, num = m.group(1), int(m.group(2))
            if ch:
                state["chain"] = ch
            proc = orig2proc.get(state["chain"], {}).get(num, num)
            return f"{ch}{proc}"
        # Alternative 2 matched: bare positive endpoint e.g. the "202" in "A201-202"
        # '-' before it is the range separator, so it's never negative here.
        num = int(m.group(3))
        proc = orig2proc.get(state["chain"], {}).get(num, num)
        return str(proc)
    # Two alternatives (tried left-to-right):
    #   1. ([A-Za-z])(-?\d+)  — chain-prefixed token, supports negative resids (e.g. B-2)
    #   2. (?<![A-Za-z\d])(\d+) — bare positive integer not preceded by letter or digit
    #      (the '-' before a bare endpoint is the range separator, not a sign)
    return re.sub(r"([A-Za-z])(-?\d+)|(?<![A-Za-z\d])(\d+)", repl, contig)

def build_input(payload: dict) -> dict:
    """Produce the RFD3 worker input dict (preprocess PDB + build spec)."""
    out = dict(payload)
    pdb_text = structure.extract_pdb_text(out)
    contig = out.pop("contigs", None) or out.pop("contig", None)
    length = out.pop("length", None)
    processed_coords = bool(out.pop("contig_processed_coords", False))

    spec: dict[str, Any] = {}
    if pdb_text:
        clean, mapping = structure.preprocess(pdb_text)
        out["input_files"] = {"input.pdb": clean}
        spec["input"] = "input.pdb"
        if contig:
            spec["contig"] = _remap_contig(str(contig), mapping, processed_coords=processed_coords)
        if length:
            spec["length"] = str(length)
    else:
        if length:
            spec["length"] = str(length)
        elif contig:
            spec["length"] = str(contig)

    hotspots = out.pop("hotspots", None) or out.pop("hotspot_res", None)
    if hotspots:
        spec["hotspots"] = hotspots
    if spec:
        out["inputs"] = {"spec-1": spec}
    out.pop("input_archive", None)
    return out
