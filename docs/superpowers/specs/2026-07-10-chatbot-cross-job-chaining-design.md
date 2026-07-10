# Chatbot Cross-Job Artifact Chaining + Connection Guide

**Date:** 2026-07-10
**Status:** Design approved, pending implementation plan

## Problem

Users want to tell the in-UI execution assistant (`/api/chat`) things like
"이 RFdiffusion 잡 결과로 DiffDock 돌려줘" (dock the ligand onto the backbone
this job produced) and have it work in one step. Today it cannot:

- `job_result` returns only artifact **metadata** (`id, file_name, kind, size`),
  never file bytes.
- `run_model`'s input files come **only** from files the user attached in the
  chat UI (`chat/loop.py` injects `attachments` into the `files` arg).

So the LLM has no way to move a prior job's output into a new job's input.
Pushing PDB bytes through the LLM context (base64) is not acceptable.

Beyond the single RFD3→DiffDock case, users should be able to compose **any**
compatible sequence of the portal's models (e.g.
RFdiffusion → ProteinMPNN → ColabFold → DiffDock), building RAPID-style
pipelines themselves. A guide surface should show what can connect to what.

## Goals

1. Extend `run_model` so the assistant can feed a prior job's output artifacts
   into a new job, resolved **server-side** (no bytes through the LLM).
2. Support **every compatible model pair**, not just structure→structure —
   driven by an explicit compatibility model (single source of truth).
3. Add an interactive `/chains` guide tab that visualizes the compatibility
   graph and offers curated example chains with "say this to the assistant"
   copy.

## Non-Goals (YAGNI)

- Automatic polling/waiting for a source job to finish. The user requests the
  next step after the previous job completes; if the source job is not
  finished, return a clear error.
- Full DAG orchestration in chat (the `/api/workflows` orchestrator already
  exists for fixed templates; this is ad-hoc chaining).
- A drag-to-build pipeline editor. The `/chains` tab is read-only guidance.

## Architecture

### 1. Compatibility model — `app/chaining.py` (new)

Single source of truth for what each pipeline produces and consumes.

```python
# role: semantic artifact role flowing between jobs
# ROLE_STRUCTURE = "structure"  (.pdb/.cif — Artifact.kind == "structure")
# ROLE_SEQUENCE  = "sequence"   (.fasta/.fa — extracted to the `sequence` arg)
# ROLE_COMPLEX   = "complex"    (docked pose output; terminal)
# ROLE_MSA       = "msa"

# For each pipeline key:
#   produces: list[str]                 roles this pipeline outputs
#   consumes: dict[str, str]            role -> delivery ("files" | "sequence")
CHAIN_META = {
    "rfdiffusion":  {"produces": ["structure"], "consumes": {"structure": "files"}},
    "proteinmpnn":  {"produces": ["sequence"],  "consumes": {"structure": "files"}},
    "colabfold":    {"produces": ["structure"], "consumes": {"sequence": "sequence"}},
    "alphafold":    {"produces": ["structure"], "consumes": {"sequence": "sequence"}},
    "esmfold":      {"produces": ["structure"], "consumes": {"sequence": "sequence"}},
    "esmfold2":     {"produces": ["structure"], "consumes": {"sequence": "sequence"}},
    "bioemu":       {"produces": ["structure"], "consumes": {"sequence": "sequence", "structure": "files"}},
    "diffdock":     {"produces": ["complex"],   "consumes": {"structure": "files"}},
    "rosetta_relax":{"produces": ["structure"], "consumes": {"structure": "files"}},
    "mmseqs":       {"produces": ["msa"],       "consumes": {"sequence": "sequence"}},
    # phastest: genome analysis, not part of the protein chain graph
}
```

Helper functions (pure, unit-testable):
- `compatible_role(src_pipeline, dst_pipeline) -> tuple[str, str] | None`
  returns `(role, delivery)` for the first role in
  `src.produces ∩ dst.consumes`, else `None`.
- `compat_graph() -> {"nodes": [...], "edges": [{from, to, role}], "examples": [...]}`
  derived from `CHAIN_META` + a small curated `EXAMPLE_CHAINS` list. This is what
  the guide endpoint returns, so the guide can never drift from the real rules.

`EXAMPLE_CHAINS` (curated, each with a title, ordered pipeline keys, and an
assistant prompt):
- De novo binder design: `rfdiffusion → proteinmpnn → colabfold → diffdock`
- Sequence design + validation: `proteinmpnn → colabfold`
- Fold + dock a known sequence: `colabfold → diffdock`
- Backbone dock: `rfdiffusion → diffdock`

### 2. `run_model` resolution — `app/mcp/tools.py`

New optional params on the `run_model` schema:
- `from_job_id: string` — a prior job whose output feeds this run.
- `source_artifact_ids: string[]` — explicit artifact ids to inject as files
  (override of auto-selection).

Resolution order inside `run_model` (has `db`, `user`):
1. If neither `from_job_id` nor `source_artifact_ids` given → current behavior.
2. Validate the source job: `_owned_job(db, user, from_job_id)`; not found →
   `{"ok": False, "error": "source job not found"}`.
3. Source job must be finished. If status is still active
   (`pending/submitted/running/queued/…`) →
   `{"ok": False, "error": "source job <id> is not finished (status=<s>)"}`.
4. Determine delivery for `(source.pipeline, target pipeline)` via
   `compatible_role`:
   - If `source_artifact_ids` given: read those artifacts' `file_path` bytes and
     inject as `input_files` (files delivery, explicit override — skips role check
     but still validates the artifacts belong to the source job).
   - Else if compatible role delivery == `"files"`: auto-select the source job's
     artifacts with the matching kind (`structure` → `kind == "structure"`),
     inject as `input_files`.
   - Else if delivery == `"sequence"`: find the source job's FASTA artifact,
     read it, extract the first record's sequence, set the `sequence` arg
     (unless the caller already supplied a non-empty `sequence`).
   - Else (no compatible role) →
     `{"ok": False, "error": "<src pipeline> output cannot feed <dst pipeline>; compatible targets: [...]"}`.
5. If files delivery selected nothing (e.g. no structure artifacts) →
   `{"ok": False, "error": "no chainable artifacts in job <id>; available: [names]"}`.
6. Combine chained inputs with any user chat attachments (both included), then
   call `job_bridge.create_step_job` as today. For DiffDock: protein PDB comes
   from the chain, ligand comes from the user's attachment/params.

`from_job_id` is a distinct arg from `files`, so `loop.py`'s
`args = {**args, "files": attachments}` injection does not clobber it. No change
to that injection is required; only the SYSTEM_PROMPT text is updated.

Artifact file bytes are read from `Artifact.file_path` (already an absolute
on-disk path; `jobs.py:download_artifact` uses it the same way).

### 3. Guide endpoint + `/chains` tab

- Backend: `GET /api/chains/compat` (new router `routers/chains.py`, registered in
  `main.py`) → returns `chaining.compat_graph()`. Auth: `get_current_user`.
- Frontend: new route `app/chains/page.tsx`.
  - Interactive node graph, **self-contained lightweight SVG** (no new heavy
    dependency such as react-flow). Nodes are pipelines laid out by role layer;
    edges are compatible connections. Clicking a node highlights its outgoing
    compatible targets and dims the rest; a side panel lists the example chains
    that include it.
  - Example-chain cards render the ordered pipeline badges and a copyable
    "say this to the assistant" prompt.
  - Header link `연결 가이드` added to the `app/page.tsx` header nav (matching the
    existing Korean labels `AI 연결` / `자가개선`).

### 4. SYSTEM_PROMPT update — `app/chat/loop.py`

Add: the assistant can chain jobs by passing `from_job_id` (and optionally
`source_artifact_ids`) to `run_model`; the server injects the prior job's
compatible outputs. For DiffDock the user must still provide the ligand
(SMILES/SDF) via an attachment or parameters. When unsure whether two models
connect, describe the compatible options (mirrors `/chains`).

## Error Handling

All chaining failures return `{"ok": False, "error": <human message>}` so the
loop feeds them back to the LLM, which explains/repairs in the user's language:
- source job not found / not owned
- source job not finished (includes status)
- incompatible pair (includes the compatible target list)
- no chainable artifacts (includes available artifact names)
- explicit `source_artifact_ids` not belonging to the source job

## Testing

Backend (`portal/backend/tests/`):
- `test_chaining.py` (new): `compatible_role` for representative pairs
  (rfdiffusion→proteinmpnn files, proteinmpnn→colabfold sequence,
  colabfold→diffdock files, rfdiffusion→mmseqs None); `compat_graph` shape
  (nodes/edges/examples present, edges only between compatible pairs).
- `test_mcp_tools.py` (extend): run_model with `from_job_id`
  files-delivery auto-select injects structure artifacts into
  `create_step_job`; sequence-delivery extracts FASTA → `sequence`;
  `source_artifact_ids` override; ownership enforced (other user's job
  rejected); not-finished error; no-chainable-artifact error; attachments +
  chained files both present; incompatible pair error.
- `test_chains_api.py` (new): `GET /api/chains/compat` returns graph + examples;
  requires auth.

Frontend (match existing test convention, verify during planning):
- `/chains` renders nodes, edges, and example-chain cards; clicking a node
  highlights its compatible targets.

## Files Touched

- `portal/backend/app/chaining.py` (new)
- `portal/backend/app/mcp/tools.py` (run_model + helpers)
- `portal/backend/app/chat/loop.py` (SYSTEM_PROMPT)
- `portal/backend/app/routers/chains.py` (new) + `app/main.py` (register)
- `portal/frontend/src/app/chains/page.tsx` (new) + `app/page.tsx` (header link)
- Tests as above

No changes to `job_bridge`, `runpod`, or the workflow orchestrator.
