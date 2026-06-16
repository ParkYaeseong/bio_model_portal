from __future__ import annotations


# The 20 standard amino acids BioEmu/RFD3 accept (IUPAC). Models reject anything
# else (e.g. 'X' for unknown residues) at input validation, after the request
# has already reached the GPU.
_STANDARD_AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")


def validate_protein_sequence(sequence: str, *, model: str) -> None:
    """Fail fast on sequences a structure model cannot accept.

    Raises ValueError with a precise, user-facing message naming the offending
    characters and positions so the failure does not have to round-trip to the
    GPU only to surface as an opaque HTTP 500.
    """
    seq = (sequence or "").strip().upper()
    if not seq:
        raise ValueError(f"{model} requires a non-empty protein sequence.")
    bad = sorted({c for c in seq if c not in _STANDARD_AMINO_ACIDS})
    if bad:
        positions = [i + 1 for i, c in enumerate(seq) if c not in _STANDARD_AMINO_ACIDS]
        preview = ", ".join(str(p) for p in positions[:10])
        if len(positions) > 10:
            preview += ", ..."
        raise ValueError(
            f"{model} only accepts the 20 standard amino acids; sequence "
            f"contains non-standard character(s) {bad} at position(s) {preview}. "
            f"Clean or trim the input sequence before redesign."
        )


def build_bioemu_input(payload: dict) -> dict:
    """Validate the protein sequence then build the BioEmu worker payload."""
    seq = str(payload.get("sequence", "") or "").strip()
    validate_protein_sequence(seq, model="BioEmu")
    out = dict(payload)
    out["sequence"] = seq
    out.pop("input_archive", None)
    return out
