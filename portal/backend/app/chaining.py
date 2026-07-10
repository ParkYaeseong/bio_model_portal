"""Cross-job chaining: which pipeline outputs can feed which pipeline inputs.

Single source of truth used by both run_model (to resolve from_job_id into a
new job's inputs) and the /chains guide endpoint (to render the compat graph).
No file IO here except reading a source FASTA to extract a sequence; callers do
the input-file byte copying.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROLE_STRUCTURE = "structure"
ROLE_SEQUENCE = "sequence"
ROLE_COMPLEX = "complex"
ROLE_MSA = "msa"

# pipeline -> {"produces": [roles], "consumes": {role: delivery}}
# delivery: how a consumed role reaches the worker — "files" (uploaded input
# archive) or "sequence" (the payload `sequence` field).
CHAIN_META: dict[str, dict] = {
    "rfdiffusion":   {"produces": [ROLE_STRUCTURE], "consumes": {ROLE_STRUCTURE: "files"}},
    "proteinmpnn":   {"produces": [ROLE_SEQUENCE],  "consumes": {ROLE_STRUCTURE: "files"}},
    "colabfold":     {"produces": [ROLE_STRUCTURE], "consumes": {ROLE_SEQUENCE: "sequence"}},
    "alphafold":     {"produces": [ROLE_STRUCTURE], "consumes": {ROLE_SEQUENCE: "sequence"}},
    "esmfold":       {"produces": [ROLE_STRUCTURE], "consumes": {ROLE_SEQUENCE: "sequence"}},
    "esmfold2":      {"produces": [ROLE_STRUCTURE], "consumes": {ROLE_SEQUENCE: "sequence"}},
    "bioemu":        {"produces": [ROLE_STRUCTURE], "consumes": {ROLE_SEQUENCE: "sequence", ROLE_STRUCTURE: "files"}},
    "diffdock":      {"produces": [ROLE_COMPLEX],   "consumes": {ROLE_STRUCTURE: "files"}},
    "rosetta_relax": {"produces": [ROLE_STRUCTURE], "consumes": {ROLE_STRUCTURE: "files"}},
    "mmseqs":        {"produces": [ROLE_MSA],       "consumes": {ROLE_SEQUENCE: "sequence"}},
}

# artifact kinds that satisfy a role delivered via files
_ROLE_ARTIFACT_KINDS = {ROLE_STRUCTURE: {"structure"}}

EXAMPLE_CHAINS = [
    {"title": "De novo 결합체 설계", "steps": ["rfdiffusion", "proteinmpnn", "colabfold", "diffdock"],
     "prompt": "RFdiffusion으로 백본을 만들고, 그 결과로 ProteinMPNN 서열 설계, ColabFold로 접은 뒤 DiffDock으로 리간드를 도킹해줘."},
    {"title": "서열 설계 + 구조 검증", "steps": ["proteinmpnn", "colabfold"],
     "prompt": "이 ProteinMPNN 잡 결과 서열을 ColabFold로 접어줘."},
    {"title": "알려진 서열 폴딩 + 도킹", "steps": ["colabfold", "diffdock"],
     "prompt": "이 ColabFold 잡 구조로 DiffDock을 돌려줘. 리간드는 첨부할게."},
    {"title": "백본 직접 도킹", "steps": ["rfdiffusion", "diffdock"],
     "prompt": "이 RFdiffusion 백본으로 DiffDock을 돌려줘. 리간드는 첨부할게."},
]


class ChainError(Exception):
    """Raised when a requested chain is invalid; message is user-facing."""


@dataclass
class ChainPlan:
    delivery: str        # "files" | "sequence"
    artifacts: list      # models.Artifact rows (files delivery)
    sequence: str | None  # extracted sequence (sequence delivery)


def compatible_role(src_pipeline: str, dst_pipeline: str) -> tuple[str, str] | None:
    src = CHAIN_META.get(src_pipeline)
    dst = CHAIN_META.get(dst_pipeline)
    if not src or not dst:
        return None
    for role in src["produces"]:
        if role in dst["consumes"]:
            return role, dst["consumes"][role]
    return None


def compatible_targets(src_pipeline: str) -> list[str]:
    return [dst for dst in CHAIN_META
            if dst != src_pipeline and compatible_role(src_pipeline, dst)]


def _first_fasta_sequence(text: str) -> str:
    seq: list[str] = []
    started = False
    for line in text.splitlines():
        if line.startswith(">"):
            if started:
                break
            started = True
            continue
        if started:
            seq.append(line.strip())
    return "".join(seq)


def plan_chain(db, source_job, target_pipeline, source_artifact_ids=None) -> ChainPlan:
    """Decide how source_job's output feeds target_pipeline. Raises ChainError.

    Reads no bytes for files delivery (returns artifact rows; caller copies
    file_path). For sequence delivery it reads the source FASTA text only.
    """
    # db is accepted for caller symmetry; this function reads only source_job + files.
    arts = list(source_job.artifacts)

    if source_artifact_ids:
        # Explicit artifact ids override auto-selection, but still only make
        # sense for a target that accepts file inputs (structure via files),
        # and the chosen artifacts must be of the expected kind.
        if target_pipeline not in CHAIN_META:
            raise ChainError(f"unknown target pipeline '{target_pipeline}'")
        files_roles = [r for r, delivery in CHAIN_META[target_pipeline]["consumes"].items()
                       if delivery == "files"]
        if not files_roles:
            raise ChainError(
                f"'{target_pipeline}' does not accept file inputs; "
                f"cannot inject explicit artifacts."
            )
        allowed_kinds = set().union(*(_ROLE_ARTIFACT_KINDS.get(r, set()) for r in files_roles))
        wanted = set(source_artifact_ids)
        chosen = [a for a in arts if a.id in wanted]
        missing = wanted - {a.id for a in chosen}
        if missing:
            raise ChainError(f"artifact(s) not found in source job: {sorted(missing)}")
        bad = [a.file_name for a in chosen if a.kind not in allowed_kinds]
        if bad:
            raise ChainError(
                f"artifact(s) {bad} are not valid file inputs for '{target_pipeline}' "
                f"(expected kinds: {sorted(allowed_kinds)})"
            )
        return ChainPlan(delivery="files", artifacts=chosen, sequence=None)

    role_delivery = compatible_role(source_job.pipeline, target_pipeline)
    if role_delivery is None:
        targets = compatible_targets(source_job.pipeline)
        raise ChainError(
            f"'{source_job.pipeline}' output cannot feed '{target_pipeline}'. "
            f"Compatible targets: {targets or 'none'}."
        )
    role, delivery = role_delivery

    if delivery == "files":
        kinds = _ROLE_ARTIFACT_KINDS.get(role, set())
        chosen = [a for a in arts if a.kind in kinds]
        if not chosen:
            raise ChainError(
                f"no {role} artifacts in source job; available: {[a.file_name for a in arts]}"
            )
        return ChainPlan(delivery="files", artifacts=chosen, sequence=None)

    fasta = next((a for a in arts if a.file_name.lower().endswith((".fa", ".fasta"))), None)
    if fasta is None:
        raise ChainError(
            f"no FASTA artifact to chain as sequence; available: {[a.file_name for a in arts]}"
        )
    seq = _first_fasta_sequence(Path(fasta.file_path).read_text())
    if not seq:
        raise ChainError(f"FASTA artifact '{fasta.file_name}' has no sequence")
    return ChainPlan(delivery="sequence", artifacts=[], sequence=seq)


def compat_graph() -> dict:
    nodes = [
        {"key": k, "produces": v["produces"], "consumes": list(v["consumes"].keys())}
        for k, v in CHAIN_META.items()
    ]
    edges = [
        {"from": src, "to": dst, "role": rd[0]}
        for src in CHAIN_META
        for dst in CHAIN_META
        if src != dst and (rd := compatible_role(src, dst))
    ]
    return {"nodes": nodes, "edges": edges, "examples": EXAMPLE_CHAINS}
