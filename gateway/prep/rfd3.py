from __future__ import annotations
import re
from typing import Any
from . import structure

def _normalize_contig_token(value: str) -> str:
    return str(value or "").replace(":", "")  # "A:1" -> "A1" (mirror protein_pipeline)

def _remap_contig(contig: str, mapping: dict, *, processed_coords: bool) -> str:
    """Dropdown/recommended contigs are already in processed coords -> just normalize.
    Custom contigs are in original PDB numbering -> remap each ChainID+resid token."""
    contig = _normalize_contig_token(contig)
    if processed_coords or not mapping:
        return contig
    orig2proc = {ch: {e["original_resseq"]: e["processed_resseq"] for e in entries}
                 for ch, entries in mapping.items()}
    def repl(m):
        ch, num = m.group(1), int(m.group(2))
        return f"{ch}{orig2proc.get(ch, {}).get(num, num)}"
    return re.sub(r"([A-Za-z])(-?\d+)", repl, contig)

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
