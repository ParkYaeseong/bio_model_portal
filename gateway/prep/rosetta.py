from __future__ import annotations

from prep import structure


def build_input(payload: dict) -> dict:
    """Prepare Rosetta Relax worker input from portal payload.

    - If payload already has pdb_content/input_pdb_content, use it directly.
    - Otherwise extract from input_archive and normalize via structure.preprocess.
    - Pass through target_id, nstruct, extra_flags, timeout_s.
    - Drop input_archive from the returned dict.
    """
    out: dict = {}

    # Determine PDB content
    if payload.get("pdb_content") or payload.get("input_pdb_content"):
        # Already inline — use as-is (no re-normalization to avoid double-processing)
        pdb_content = payload.get("pdb_content") or payload.get("input_pdb_content")
    else:
        pdb_text = structure.extract_pdb_text(payload)
        if pdb_text is not None:
            clean, _ = structure.preprocess(pdb_text)
            pdb_content = clean
        else:
            pdb_content = None

    if pdb_content is not None:
        out["pdb_content"] = pdb_content

    # target_id: explicit > pdb_name > "target"
    target_id = (
        payload.get("target_id")
        or payload.get("pdb_name")
        or "target"
    )
    out["target_id"] = target_id

    # Pass-through optional worker keys
    for key in ("nstruct", "extra_flags", "timeout_s"):
        if key in payload:
            out[key] = payload[key]

    return out
