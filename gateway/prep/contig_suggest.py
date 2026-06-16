from __future__ import annotations
from typing import Any
from bio import pdb
from . import structure

def _compress_ranges(nums: list[int]) -> list[str]:
    """[1,2,3,7,8] -> ['1-3','7-8']."""
    if not nums:
        return []
    nums = sorted(set(nums))
    out, start, prev = [], nums[0], nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n; continue
        out.append(f"{start}-{prev}" if start != prev else f"{start}")
        start = prev = n
    out.append(f"{start}-{prev}" if start != prev else f"{start}")
    return out

def suggest(pdb_text: str) -> dict[str, Any]:
    """Dropdown options in PROCESSED coords + ligand-aware recommendation."""
    clean, _mapping = structure.preprocess(pdb_text)
    residues = pdb.residues_by_chain(clean)  # processed coords
    chains = sorted(residues.keys())
    options: list[dict[str, Any]] = []
    for ch in chains:
        nums = [r.resseq for r in residues[ch]]
        if nums:
            options.append({"id": f"chain_{ch}", "label": f"체인 {ch} 전체",
                            "contig": f"{ch}{min(nums)}-{max(nums)}", "recommended": False})
    if len([o for o in options if o["id"].startswith("chain_")]) > 1:
        allc = ",".join(o["contig"] for o in options if o["id"].startswith("chain_"))
        options.append({"id": "all_chains", "label": "전체 체인", "contig": allc, "recommended": False})
    has_ligand = pdb.ligand_atoms_present(clean)
    if has_ligand:
        mask = pdb.ligand_proximity_mask(clean, distance_angstrom=6.0)  # {chain: [index]}
        toks: list[str] = []
        for ch in sorted(mask):
            idx2resseq = {r.index: r.resseq for r in residues.get(ch, [])}
            nums = sorted(idx2resseq.get(i, i) for i in mask[ch])
            for rng in _compress_ranges(nums):
                toks.append(f"{ch}{rng}")
        if toks:
            options.append({"id": "ligand_motif", "label": "리간드 주변 모티프 (~6Å)",
                            "contig": ",".join(toks), "recommended": False})
    options.append({"id": "custom", "label": "직접 입력", "contig": "", "recommended": False})
    rec_id = "ligand_motif" if any(o["id"] == "ligand_motif" for o in options) else (
        f"chain_{chains[0]}" if chains else "custom")
    for o in options:
        o["recommended"] = (o["id"] == rec_id)
    return {"chains": chains, "options": options, "processed_coords": True}
