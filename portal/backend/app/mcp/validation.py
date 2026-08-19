"""Pre-submit validation for agent-driven runs (MCP tools / in-UI chatbot).

The UI form enforces each model's required inputs before it POSTs; the MCP and
chatbot paths had no equivalent, so a call missing a structure, a sequence or a
required parameter was accepted, submitted, and only failed minutes later inside
the gateway adapter (or, worse, ran to completion on wrong inputs). Everything
checked here is a rule the gateway/worker enforces anyway -- this just moves the
failure to submit time, with a message that says what to send instead.

Called from `chaining_exec.submit_chained` AFTER chained inputs are resolved, so
`input_files`/`sequence` are the effective inputs the worker will see.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..runpod import PIPELINES

STRUCTURE_SUFFIXES = {".pdb", ".cif", ".mmcif", ".ent"}
FASTA_SUFFIXES = {".fasta", ".fa", ".faa", ".fna", ".seq"}
LIGAND_SUFFIXES = {".sdf", ".mol", ".mol2"}

# Models whose input is an uploaded structure (`requires_archive` in the
# catalog) -> what that structure has to be.
_STRUCTURE_INPUT = {
    "antifold": "an antibody structure (.pdb/.cif) containing the heavy/light "
                "(or nanobody) chains",
    "proteinmpnn": "a backbone structure (.pdb/.cif)",
    "ppiformer": "a protein-protein complex structure (.pdb/.cif)",
    "rosetta_relax": "a structure (.pdb/.cif)",
    "diffdock": "a receptor structure (.pdb/.cif)",
}

# Models that fold/analyse a sequence. They also accept a PDB/CIF or FASTA
# upload, from which the gateway recovers the sequence.
_SEQUENCE_INPUT = {
    "alphafold", "alphafold3", "boltz2", "colabfold",
    "esmfold", "esmfold2", "bioemu", "mmseqs", "anarcii",
}

# Characters a protein sequence may contain. Ambiguity codes (BJOUXZ) are
# allowed through here -- some workers accept them -- but digits, '(' etc. are
# always a mis-pasted input.
_SEQUENCE_ALPHABET = set("ACDEFGHIKLMNPQRSTVWYXBZJUO*-:/ \t\r\n")

# Models that fold exactly one chain. ESMFold's tokenizer crashes on a
# ':'-joined complex, and AlphaFold2 takes its chains as separate FASTA records,
# not as one joined string -- so a complex has to be routed elsewhere.
_MONOMER_ONLY = {
    "esmfold": "ColabFold, AlphaFold3 or Boltz-2",
    "esmfold2": "ColabFold, AlphaFold3 or Boltz-2",
    "bioemu": "ColabFold, AlphaFold3 or Boltz-2",
    "alphafold": "a FASTA with one '>header' record per chain (model_preset='multimer')",
}

# AntiFold region tokens, mirroring gateway/prep/antifold.py.
_ANTIFOLD_REGIONS = {
    "all", "allH", "allL", "FWH", "FWL", "CDRH", "CDRL",
    "FW1", "FWH1", "FWL1", "CDR1", "CDRH1", "CDRL1",
    "FW2", "FWH2", "FWL2", "CDR2", "CDRH2", "CDRL2",
    "FW3", "FWH3", "FWL3", "CDR3", "CDRH3", "CDRL3",
    "FW4", "FWH4", "FWL4", "CDR3H",
}


# Field types whose `placeholder` is a usable default value, not a format hint.
_AUTOFILLABLE_TYPES = {"number", "date"}


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _names(paths: list[Path]) -> list[str]:
    return [Path(p).name for p in paths]


def _with_suffix(paths: list[Path], suffixes: set[str]) -> list[Path]:
    return [p for p in paths if Path(p).suffix.lower() in suffixes]


def _fasta_record_count(text: str) -> int:
    headers = sum(1 for line in text.splitlines() if line.startswith(">"))
    if headers:
        return headers
    return 1 if text.strip() else 0


def _check_required_params(pipeline_def, params: dict) -> dict:
    """Fill missing required params that have a documented recommended default
    (the field's placeholder, which is what the tool descriptions tell agents to
    fall back to) and reject the ones that have no safe default.

    Returns the values filled in, so the caller can report them rather than
    quietly deciding a parameter on the user's behalf.
    """
    applied: dict = {}
    for field in pipeline_def.input_fields:
        if not field.required or not _blank(params.get(field.name)):
            continue
        # Only number/date placeholders are concrete recommended values
        # (num_designs "1", max_template_date "2023-09-01"). A text/password
        # placeholder is a format hint ("biohub_..."), never something to submit.
        if field.placeholder and field.field_type in _AUTOFILLABLE_TYPES:
            params[field.name] = field.placeholder
            applied[field.name] = field.placeholder
            continue
        hint = ""
        if field.options:
            hint = " (one of: " + ", ".join(o["value"] for o in field.options if o["value"]) + ")"
        elif field.placeholder:
            hint = f" (expected form: {field.placeholder})"
        raise ValueError(
            f"{pipeline_def.label}: required parameter '{field.name}' is missing{hint}. "
            f"Ask the user which value to use — this model has no safe default."
        )
    return applied


def _check_param_types(pipeline_def, params: dict) -> None:
    for field in pipeline_def.input_fields:
        value = params.get(field.name)
        if _blank(value):
            continue
        if field.field_type == "number":
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise ValueError(
                    f"{pipeline_def.label}: parameter '{field.name}' must be a number, got {value!r}."
                ) from None
            if field.minimum is not None and number < field.minimum:
                raise ValueError(
                    f"{pipeline_def.label}: parameter '{field.name}' must be >= "
                    f"{field.minimum:g}, got {value!r}."
                )
        elif field.field_type == "select" and field.options:
            allowed = {o["value"] for o in field.options}
            if str(value) not in allowed:
                raise ValueError(
                    f"{pipeline_def.label}: parameter '{field.name}' must be one of "
                    f"{sorted(v for v in allowed if v)}, got {value!r}."
                )


def _check_sequence_text(label: str, sequence: str) -> None:
    body = "".join(
        line for line in sequence.splitlines(keepends=True) if not line.startswith(">")
    )
    bad = sorted({c for c in body.upper() if c not in _SEQUENCE_ALPHABET})
    if bad:
        raise ValueError(
            f"{label}: the sequence contains character(s) {bad} that are not amino "
            f"acids. Pass a plain protein sequence (or a FASTA), not a file path, "
            f"JSON, or a nucleotide/structure blob."
        )
    if not body.strip():
        raise ValueError(f"{label}: the sequence is empty.")


def _check_structure_input(key, pipeline_def, input_files: list[Path]) -> None:
    if _with_suffix(input_files, STRUCTURE_SUFFIXES):
        return
    wants = _STRUCTURE_INPUT[key]
    if input_files:
        raise ValueError(
            f"{pipeline_def.label} needs {wants}, but the files provided are "
            f"{_names(input_files)}. Attach the structure itself (files=[{{'name': "
            f"'x.pdb', 'text'|'base64'|'path': ...}}]) or chain from a job that "
            f"produced one with from_job_id."
        )
    raise ValueError(
        f"{pipeline_def.label} needs {wants}. Provide it in `files` with real "
        f"content (base64/text/path/file_id — a bare 'name' is NOT read from disk), "
        f"or pass from_job_id to reuse a previous job's structure."
    )


def _check_sequence_input(key, pipeline_def, params: dict, sequence, input_files) -> None:
    if key == "alphafold3" and not _blank(params.get("af3_json")):
        return  # AF3's raw fold-input JSON supplies its own sequences/ligands
    usable = _with_suffix(input_files, STRUCTURE_SUFFIXES | FASTA_SUFFIXES)
    if not _blank(sequence):
        _check_sequence_text(pipeline_def.label, str(sequence))
        if key in _MONOMER_ONLY and any(c in str(sequence) for c in ":/"):
            raise ValueError(
                f"{pipeline_def.label} folds a single chain, but the sequence has a "
                f"chain break (':' or '/'). Use {_MONOMER_ONLY[key]} for a complex, "
                f"or pass just one chain."
            )
        return
    if usable:
        return
    extra = f" The files provided are {_names(input_files)}." if input_files else ""
    raise ValueError(
        f"{pipeline_def.label} needs a protein sequence. Pass sequence='...' (use "
        f"':' between chains for a complex), attach a FASTA/PDB in `files`, or "
        f"chain from a job that produced a sequence with from_job_id.{extra}"
    )


def _check_alphafold(params: dict, sequence, input_files: list[Path]) -> None:
    if str(params.get("model_preset") or "") != "multimer":
        return
    records = 0
    if not _blank(sequence):
        records = _fasta_record_count(str(sequence))
    for path in _with_suffix(input_files, FASTA_SUFFIXES):
        records = max(records, _fasta_record_count(Path(path).read_text(errors="replace")))
    if records < 2:
        raise ValueError(
            "AlphaFold2 multimer needs at least two chains: pass a FASTA with one "
            "'>header' record per chain (as the sequence, or as an attached .fasta "
            "file). For a single chain use model_preset='monomer'."
        )


def _check_diffdock(params: dict, input_files: list[Path]) -> None:
    if params.get("protein_ligand_csv") or params.get("jobs"):
        return  # already a worker-ready payload (the UI builds this)
    if _blank(params.get("ligand_smiles")) and _blank(params.get("ligand_sdf")) \
            and not _with_suffix(input_files, LIGAND_SUFFIXES):
        raise ValueError(
            "DiffDock needs a ligand: set parameters.ligand_smiles='<SMILES>', or "
            "attach an .sdf file in `files`."
        )


def _check_phastest(params: dict, input_files: list[Path]) -> None:
    input_type = str(params.get("input_type") or "")
    mode = str(params.get("mode") or "")
    if input_type not in {"fasta", "contig", "genbank"}:
        raise ValueError(
            "PHASTEST: parameters.input_type must be 'fasta', 'contig' or 'genbank'."
        )
    if mode not in {"lite", "deep"}:
        raise ValueError("PHASTEST: parameters.mode must be 'lite' or 'deep'.")
    if _blank(params.get("sample_name")):
        raise ValueError("PHASTEST: parameters.sample_name is required.")
    if input_type == "genbank":
        if _blank(params.get("accession")):
            raise ValueError("PHASTEST: parameters.accession is required for GenBank input.")
        return
    if not input_files:
        raise ValueError(
            f"PHASTEST '{input_type}' input needs an uploaded genome file in `files`."
        )


def _check_antifold(params: dict) -> None:
    regions = str(params.get("regions") or "").strip()
    if regions:
        bad = [t for t in regions.split() if t not in _ANTIFOLD_REGIONS]
        if bad:
            raise ValueError(
                f"AntiFold: unknown region token(s) {bad}. Valid tokens: "
                f"{sorted(_ANTIFOLD_REGIONS)}."
            )
    for name in ("heavy_chain", "light_chain", "nanobody_chain", "antigen_chain"):
        value = str(params.get(name) or "").strip()
        if value and len(value) != 1:
            raise ValueError(
                f"AntiFold: parameter '{name}' must be a single PDB chain id "
                f"(e.g. 'H'), got {value!r}."
            )
    chains = [str(params.get(n) or "").strip() for n in ("heavy_chain", "light_chain", "antigen_chain")]
    named = [c for c in chains if c]
    if len(named) != len(set(named)):
        raise ValueError(
            "AntiFold: heavy_chain, light_chain and antigen_chain must be different chains."
        )


def _check_boltz2(params: dict) -> None:
    affinity = str(params.get("predict_affinity") or "").lower() in {"true", "1", "yes"}
    if affinity and _blank(params.get("ligand_smiles")):
        raise ValueError(
            "Boltz-2: predict_affinity='true' needs parameters.ligand_smiles "
            "(affinity is predicted for a protein-ligand complex)."
        )


def _check_alphafold3(params: dict) -> None:
    raw = params.get("af3_json")
    if _blank(raw) or not isinstance(raw, str):
        return
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"AlphaFold3: af3_json is not valid JSON ({exc}).") from None
    if not isinstance(parsed, dict):
        raise ValueError("AlphaFold3: af3_json must be a JSON object (AF3 fold-input schema).")


def _check_ppiformer(params: dict) -> None:
    if _blank(params.get("mutations")):
        raise ValueError(
            "PPIformer: parameters.mutations is required — one candidate per line "
            "in <wild-type><chain><position><mutant> form (e.g. 'YH33W'); several "
            "mutations on one line are scored as a single combination variant."
        )


def _check_rfdiffusion(params: dict, input_files: list[Path]) -> None:
    if input_files or params.get("length") or params.get("contigs") or params.get("contig"):
        return
    raise ValueError(
        "RFdiffusion needs a design spec: set 'length' for de novo (recommended: 100), "
        "or provide a PDB file with 'contigs' for motif scaffolding. A sequence is not a "
        "valid RFdiffusion input. Ask the user for a length, or offer the recommended "
        "default (length=100) and confirm before running."
    )


def validate_run(
    pipeline: str,
    params: dict,
    sequence: str | None,
    input_files: list[Path],
) -> dict:
    """Raise ValueError if this submission cannot possibly run; return the
    recommended defaults filled in for missing required params (so the caller can
    tell the user). Inputs are the effective ones (chained artifacts already
    staged into `input_files`)."""
    pipeline_def = PIPELINES[pipeline]
    params = params or {}
    input_files = list(input_files or [])

    if pipeline in _STRUCTURE_INPUT:
        _check_structure_input(pipeline, pipeline_def, input_files)
    if pipeline in _SEQUENCE_INPUT:
        _check_sequence_input(pipeline, pipeline_def, params, sequence, input_files)

    checks = {
        "alphafold": lambda: _check_alphafold(params, sequence, input_files),
        "alphafold3": lambda: _check_alphafold3(params),
        "antifold": lambda: _check_antifold(params),
        "boltz2": lambda: _check_boltz2(params),
        "diffdock": lambda: _check_diffdock(params, input_files),
        "phastest": lambda: _check_phastest(params, input_files),
        "ppiformer": lambda: _check_ppiformer(params),
        "rfdiffusion": lambda: _check_rfdiffusion(params, input_files),
    }
    check = checks.get(pipeline)
    if check:
        check()

    # Generic catalog rules last: a missing structure/sequence or a model-specific
    # rule is the more useful thing to report first.
    applied = _check_required_params(pipeline_def, params)
    _check_param_types(pipeline_def, params)
    return applied
