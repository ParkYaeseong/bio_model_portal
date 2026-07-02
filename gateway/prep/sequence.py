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


def sequence_from_structure(payload: dict) -> str:
    """Best-effort: derive a single-chain sequence from an uploaded PDB/CIF.

    BioEmu samples conformational ensembles from a SEQUENCE, not a structure. If
    the user uploaded a PDB instead of typing a sequence, recover the sequence
    (longest chain — the main protein) so the job can run.
    """
    from bio import pdb as _pdb  # vendored, pure stdlib
    from . import structure

    pdb_text = structure.extract_pdb_text(payload)
    if not pdb_text:
        return ""
    by_chain = _pdb.sequence_by_chain(_pdb.normalize_structure_text(pdb_text))
    if not by_chain:
        return ""
    return max(by_chain.values(), key=len).strip()


_FASTA_SUFFIXES = (".fasta", ".fa", ".faa", ".fna")


def _archive_has_fasta(input_archive: object) -> bool:
    """True when input_archive carries FASTA input (not just a PDB/CIF).

    AF2 multimer chains arrive as a FASTA file inside input_archive rather than
    as an inline `sequence` — the portal tags such archives with kind
    fasta_dir/fasta_paths (and lists the .fasta file_names)."""
    if not isinstance(input_archive, dict):
        return False
    if input_archive.get("kind") in {"fasta_dir", "fasta_paths"}:
        return True
    names = input_archive.get("file_names") or []
    if isinstance(names, str):
        names = [names]
    return any(str(n).lower().endswith(_FASTA_SUFFIXES) for n in names)


def build_folding_input(payload: dict, *, model: str) -> dict:
    """Sequence-input models (ESMFold/ColabFold): pass the sequence through, or
    recover it from an uploaded PDB/CIF (longest chain) when none was typed."""
    from .defaults import apply_defaults

    out = dict(payload)
    has_seq = bool(str(out.get("sequence", "") or "").strip()) or bool(out.get("sequences"))
    if not has_seq:
        seq = sequence_from_structure(out)
        if seq:
            out["sequence"] = seq
        elif model == "AlphaFold2" and _archive_has_fasta(out.get("input_archive")):
            # AF2 multimer: chains are delivered as a FASTA inside input_archive,
            # which the RunPod AF2 worker consumes directly. Pass the archive
            # through instead of failing / dropping it.
            return out
        else:
            raise ValueError(
                f"{model} requires a protein sequence — type one or upload a FASTA/PDB."
            )
    # ColabFold has known, worker-accepted knobs; pin their defaults. ESMFold and
    # AlphaFold2 are left untouched (worker param names unverified / UI-required).
    if model == "ColabFold":
        out = apply_defaults(out, "colabfold")
    out.pop("input_archive", None)
    return out


# Portal AF2 knob -> run_alphafold.py flag. Stock DeepMind AlphaFold (absl flags)
# has NO recycle or model-count flag; recycles are hardcoded and all 5 models run.
# The only worker-verified tunables are these two.
_AF2_FLAG_FIELDS = (
    ("models_to_relax", "models_to_relax"),
    ("num_multimer_predictions_per_model", "num_multimer_predictions_per_model"),
)


def assemble_alphafold_flags(payload: dict) -> dict:
    """Fold the portal's AF2 knobs into the single `alphafold_extra_flags` CLI
    string the DeepMind AlphaFold worker actually reads.

    The worker (run_alphafold.py) consumes `alphafold_extra_flags` only — the
    portal historically sent the free-text field as `extra_flags`, which the
    worker silently ignored. This also maps the dedicated knobs
    (models_to_relax, num_multimer_predictions_per_model) to their flags, and
    drops the portal-only keys so the worker never sees unknown payload fields.
    A flag a user already typed in the free-text field wins over the knob.
    """
    out = dict(payload)
    user = " ".join(
        str(out.pop(key, "") or "").strip()
        for key in ("alphafold_extra_flags", "extra_flags")
    ).strip()
    parts: list[str] = []
    for param_key, flag in _AF2_FLAG_FIELDS:
        val = out.pop(param_key, None)
        if val is None or (isinstance(val, str) and not val.strip()):
            continue
        if f"--{flag}" in user:  # user set it explicitly in the free-text field
            continue
        if param_key == "num_multimer_predictions_per_model":
            try:
                val = int(val)
            except (TypeError, ValueError):
                continue
            if val < 1:
                continue
        parts.append(f"--{flag}={val}")
    combined = " ".join([*parts, user]).strip()
    if combined:
        out["alphafold_extra_flags"] = combined
    return out


def build_bioemu_input(payload: dict) -> dict:
    """Validate the protein sequence then build the BioEmu worker payload.

    Accepts either a typed `sequence` or an uploaded structure (PDB/CIF) — in the
    latter case the sequence is extracted from the structure's longest chain.
    """
    from .defaults import apply_defaults

    seq = str(payload.get("sequence", "") or "").strip()
    if not seq:
        seq = sequence_from_structure(payload)
    validate_protein_sequence(seq, model="BioEmu")
    out = apply_defaults(dict(payload), "bioemu")  # pin num_samples / model_name
    out["sequence"] = seq
    out.pop("input_archive", None)
    return out
