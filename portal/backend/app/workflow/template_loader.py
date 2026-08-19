from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_TEMPLATE_DIR = Path(__file__).parent / "templates"


@lru_cache(maxsize=None)
def load_template(template_key: str) -> dict:
    """Load a DAG template JSON by key. Raises KeyError if missing."""
    path = _TEMPLATE_DIR / f"{template_key}.json"
    if not path.exists():
        raise KeyError(f"unknown workflow template: {template_key}")
    return json.loads(path.read_text(encoding="utf-8"))


def ordered_steps(template: dict) -> list[dict]:
    """Topologically order nodes by the edge list (linear DAG for rapid_v1)."""
    nodes = {n["id"]: n for n in template["nodes"]}
    incoming = {nid: 0 for nid in nodes}
    adj: dict[str, list[str]] = {nid: [] for nid in nodes}
    for e in template["edges"]:
        adj[e["from"]].append(e["to"])
        incoming[e["to"]] += 1
    ready = [nid for nid in nodes if incoming[nid] == 0]
    ordered: list[str] = []
    seen = set()
    while ready:
        nid = ready.pop(0)
        if nid in seen:
            continue
        seen.add(nid)
        ordered.append(nid)
        for nxt in adj[nid]:
            incoming[nxt] -= 1
            if incoming[nxt] == 0:
                ready.append(nxt)
    return [nodes[nid] for nid in ordered]
