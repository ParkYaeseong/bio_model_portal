from __future__ import annotations

import hashlib

# Real-shaped interface: a future SolubilityClient.score(sequence) -> float in [0,1]
# would drop in here. MVP uses a deterministic hash so runs are reproducible.


def score(sequence: str) -> float:
    seq = (sequence or "").strip().upper()
    if not seq:
        return 0.0
    digest = hashlib.sha256(seq.encode()).digest()
    return int.from_bytes(digest[:4], "big") / 0xFFFFFFFF


def filter_top_k(candidates: list[dict], top_k: int) -> list[dict]:
    """Attach soluprot_score to each candidate, sort desc, keep top_k."""
    scored = [
        {**c, "soluprot_score": round(score(c.get("sequence", "")), 4)}
        for c in candidates
    ]
    scored.sort(key=lambda c: c["soluprot_score"], reverse=True)
    return scored[: max(0, int(top_k))]
