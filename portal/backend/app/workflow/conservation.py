from __future__ import annotations

from collections import Counter


def column_identity(column: list[str]) -> float:
    """Fraction of the most common residue in a column (0..100)."""
    residues = [c for c in column if c not in ("-", ".", " ")]
    if not residues:
        return 0.0
    most = Counter(residues).most_common(1)[0][1]
    return 100.0 * most / len(residues)


def fixed_positions(msa: list[str], tiers: list[int]) -> dict[str, list[int]]:
    """Return, per conservation tier, the 1-indexed positions whose column
    identity >= tier. Positions are fixed (kept) during ProteinMPNN design."""
    result: dict[str, list[int]] = {str(t): [] for t in tiers}
    if not msa:
        return result
    width = min(len(s) for s in msa)
    for i in range(width):
        ident = column_identity([s[i] for s in msa])
        for t in tiers:
            if ident >= t:
                result[str(t)].append(i + 1)
    return result
