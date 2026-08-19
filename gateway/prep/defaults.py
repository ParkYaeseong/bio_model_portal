from __future__ import annotations

from typing import Any

# Explicit, pinned model defaults.
#
# The portal UI exposes only a few knobs per model; everything else used to fall
# through to whatever default the remote GPU worker (the underlying tool) happens
# to use — invisible and untunable from this codebase. These tables make the
# important defaults explicit here so they are visible, version-controlled, and
# trivial to surface in the UI later.
#
# SAFETY RULES for what may live here:
#   * Only keys the worker is already known to accept — i.e. keys the portal UI
#     sends, or keys already whitelisted in a prep `_PASSTHROUGH` list. This
#     guarantees pinning a default can never introduce an "unknown argument"
#     error at the worker.
#   * Values mirror each tool's documented default, so making them explicit does
#     NOT change behavior versus the old implicit-worker-default path.
#   * Defaults are applied only when the caller left the value absent/blank, so
#     a user-supplied value always wins.
#
# Deliberately NOT pinned here (worker param names unverified or not overridable
# from the gateway): RFD3 diffusion internals, and DiffDock
# inference_steps/samples_per_complex -- confirmed live 2026-08-10 that
# appending --samples_per_complex=N to cmd has NO effect (the worker's
# default_inference_args.yaml value wins regardless); fixing this needs a
# change to the worker wrapper on the GPU host, which this repo does not
# vendor. Do not add UI fields for these until that's fixed, or they'll look
# like they work and silently do nothing.
#
# ESMFold's num_recycles/chunk_size ARE verified (2026-08-10, live against the
# real worker) and exposed as portal InputFields -- see runpod.py. They are not
# listed in MODEL_DEFAULTS below because there's nothing to silently pin: the
# UI already surfaces them, so the user's value (or its absence) rides through
# build_folding_input() unchanged.
MODEL_DEFAULTS: dict[str, dict[str, Any]] = {
    "proteinmpnn": {
        "num_seq_per_target": 1,   # ProteinMPNN default
        "sampling_temp": 0.1,      # ProteinMPNN default
        "batch_size": 1,           # ProteinMPNN default
        "backbone_noise": 0.0,     # ProteinMPNN default
        "use_soluble_model": True,  # matches RAPID's own client default
    },
    "colabfold": {
        "num_recycle": 3,                  # ColabFold default
        "num_models": 5,                   # ColabFold default
        "msa_mode": "mmseqs2_uniref_env",  # ColabFold default
    },
    "bioemu": {
        "num_samples": 10,             # conservative default (UI normally supplies this)
        "model_name": "bioemu-v1.1",   # only shipped model
    },
    "rosetta_relax": {
        "nstruct": 1,   # FastRelax default
    },
}


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def apply_defaults(params: dict, model: str) -> dict:
    """Return a copy of params with this model's pinned defaults filled in.

    Only fills keys that are absent or blank — a caller/UI-supplied value is
    never overwritten.
    """
    out = dict(params)
    for key, value in MODEL_DEFAULTS.get(model, {}).items():
        if _is_blank(out.get(key)):
            out[key] = value
    return out
