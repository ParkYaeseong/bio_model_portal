from __future__ import annotations
import re
from typing import Any
from . import structure

def _normalize_contig_token(value: str) -> str:
    return str(value or "").replace(":", "")  # "A:1" -> "A1" (mirror protein_pipeline)

def _recommended_contig(pdb_text: str) -> str | None:
    """The contig the UI would pre-select for this PDB (ligand motif if present,
    else the first chain). Returns None if suggestion fails so callers fall back
    to the pre-existing behavior."""
    try:
        from . import contig_suggest
        sugg = contig_suggest.suggest(pdb_text)
        rec = next((o for o in sugg.get("options", [])
                    if o.get("recommended") and o.get("contig")), None)
        return rec["contig"] if rec else None
    except Exception:
        return None

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
        if not contig:
            # A PDB was uploaded with no contig. The UI form runs contig
            # suggestion and pre-selects a recommended contig; the chat/MCP path
            # does not, so it used to send an input PDB that no contig references
            # — which RFD3 rejects ("Input provided but unused in composition
            # specification"). Auto-fill the same recommended contig the UI would
            # pick so the input is actually used.
            rec = _recommended_contig(pdb_text)
            if rec:
                contig = rec
                processed_coords = True  # suggestion contigs are in processed coords
                length = None            # a motif contig supersedes a bare de-novo length
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
    # The worker reads partial_t from the per-design spec (spec["partial_t"]),
    # not from the top-level payload. Without this pop it stays a stray
    # top-level key here and the worker never sees it, silently running full
    # diffusion regardless of what was requested.
    partial_t = out.pop("partial_t", None) or out.pop("partial_T", None)
    if partial_t is not None:
        spec["partial_t"] = partial_t
    if spec:
        out["inputs"] = {"spec-1": spec}
    out.pop("input_archive", None)
    return out
