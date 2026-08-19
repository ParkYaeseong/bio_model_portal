from __future__ import annotations

import json

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


def sequence_from_structure(payload: dict, *, multimer: bool = False) -> str:
    """Best-effort: derive a sequence from an uploaded PDB/CIF.

    BioEmu/ESMFold sample/fold a single chain, so the default is the longest
    chain (the main protein) — same as before. Multimer-capable models
    (ColabFold, AlphaFold3, Boltz2) pass multimer=True to instead recover every
    chain, ':'-joined in file order, so a multi-chain PDB upload doesn't
    silently collapse to just its longest chain.
    """
    from bio import pdb as _pdb  # vendored, pure stdlib
    from . import structure

    pdb_text = structure.extract_pdb_text(payload)
    if not pdb_text:
        return ""
    by_chain = _pdb.sequence_by_chain(_pdb.normalize_structure_text(pdb_text))
    if not by_chain:
        return ""
    if multimer and len(by_chain) > 1:
        return ":".join(seq.strip() for seq in by_chain.values())
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


def _parse_fasta_sequences(text: str) -> list[str]:
    """One sequence per '>' record (headers dropped, whitespace removed).

    A body with no '>' header is treated as a single bare sequence."""
    records: list[str] = []
    current: list[str] = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith(">"):
            if current:
                records.append("".join(current))
                current = []
            continue
        current.append(s)
    if current:
        records.append("".join(current))
    return [r.strip().upper() for r in records if r.strip()]


def sequences_from_fasta_archive(payload: dict) -> list[str]:
    """Return the per-chain sequences from an uploaded FASTA archive (empty if
    none). ESMFold/ColabFold take an inline sequence, not a FASTA file — the
    caller turns this list into the worker-appropriate inline form."""
    from . import structure

    fasta_text = structure.extract_fasta_text(payload)
    if not fasta_text:
        return []
    return _parse_fasta_sequences(fasta_text)


def build_folding_input(payload: dict, *, model: str) -> dict:
    """Sequence-input models (ESMFold/ColabFold): pass the sequence through, or
    recover it from an uploaded PDB/CIF (longest chain) or FASTA (one record =>
    monomer, many => ':'-joined multimer) when none was typed."""
    from .defaults import apply_defaults

    out = dict(payload)

    # AF3's advanced escape hatch: a full AF3 fold-input JSON (ligands, ions,
    # RNA/DNA, modified residues, custom MSA/templates, covalent bonds,
    # userCCD, ...) that the worker passes straight through, bypassing every
    # other field here entirely -- so skip sequence derivation altogether.
    af3_json = out.get("af3_json")
    if isinstance(af3_json, str):
        if not af3_json.strip():
            out.pop("af3_json", None)  # blank textarea -> drop, don't send an empty string
        else:
            try:
                parsed = json.loads(af3_json)
            except json.JSONDecodeError as exc:
                raise ValueError(f"AF3 입력 JSON 형식이 올바르지 않습니다: {exc}") from exc
            if not isinstance(parsed, dict):
                raise ValueError("AF3 입력 JSON은 객체(object)여야 합니다.")
            out["af3_json"] = parsed
            out.pop("input_archive", None)
            return out
    elif isinstance(af3_json, dict):
        out.pop("input_archive", None)
        return out

    has_seq = bool(str(out.get("sequence", "") or "").strip()) or bool(out.get("sequences"))
    if not has_seq:
        # Only these two accept a ':'-joined complex directly in `sequence`.
        # ESMFold can't fold one (see the multimer rejection below), and AF2's
        # multimer path is a separate FASTA-archive/'/' convention entirely —
        # feeding it a colon-joined string here would just fail its own
        # validation, so both keep the old longest-chain-only extraction.
        seq = sequence_from_structure(out, multimer=model in ("ColabFold", "AlphaFold3", "Boltz2"))
        if seq:
            out["sequence"] = seq
        elif model == "AlphaFold2" and _archive_has_fasta(out.get("input_archive")):
            # AF2 multimer: chains are delivered as a FASTA inside input_archive,
            # which the RunPod AF2 worker consumes directly. Pass the archive
            # through instead of failing / dropping it.
            return out
        else:
            fasta_seqs = sequences_from_fasta_archive(out)
            if not fasta_seqs:
                raise ValueError(
                    f"{model} requires a protein sequence — type one or upload a FASTA/PDB."
                )
            if len(fasta_seqs) == 1:
                # Single record => monomer (works for both ESMFold and ColabFold).
                out["sequence"] = fasta_seqs[0]
            elif model == "ESMFold":
                # This ESMFold worker (facebook/esmfold_v1) folds a single chain;
                # a ':'-joined sequence crashes its tokenizer. Multimers must go
                # to ColabFold/AlphaFold2.
                raise ValueError(
                    "ESMFold는 단일 체인 구조만 예측합니다. 멀티머(복합체)는 "
                    "ColabFold 또는 AlphaFold2를 사용하세요."
                )
            else:
                # ColabFold: ':'-joined chains => auto-runs the multimer model.
                out["sequence"] = ":".join(fasta_seqs)
    # ColabFold has known, worker-accepted knobs; pin their defaults. ESMFold's
    # num_recycles/chunk_size are portal InputFields already, so they ride
    # through in `out` unchanged -- no pinning needed. AlphaFold2 is left
    # untouched (worker param names unverified / UI-required).
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
