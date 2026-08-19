"""Portal input -> PPIformer ddG worker input.

The worker wants {"structure": <pdb text>, "candidates": [{"id", "mutations"}]}.
The portal gives an uploaded structure plus free-text fields, so this pulls the
PDB out of the archive and turns the mutation box into candidates.

Mutation syntax is PPIformer's own: <wild-type><chain><position><mutant>, e.g.
``YH33W``. One line is one candidate; several mutations on a line (comma or
space separated) are one candidate carrying all of them, which is how a
combination variant is scored as a single ddG rather than as separate points.
"""

from __future__ import annotations


def _parse_candidates(text: str) -> list[dict]:
    candidates: list[dict] = []
    for index, line in enumerate(str(text or "").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        muts = [m for m in line.replace(",", " ").split() if m]
        if muts:
            candidates.append({"id": f"cand_{index}", "mutations": muts})
    return candidates


def build_input(payload: dict) -> dict:
    from adapters import _extract_archive, _pick_pdb

    out = dict(payload)
    if out.get("structure") and out.get("candidates"):
        out.pop("input_archive", None)
        return out

    if not out.get("structure"):
        picked = _pick_pdb(_extract_archive(out))
        if picked:
            out["structure"] = picked[1].decode("utf-8", errors="replace")

    if not out.get("candidates"):
        out["candidates"] = _parse_candidates(out.pop("mutations", ""))

    out.pop("mutations", None)
    out.pop("input_archive", None)
    return out
