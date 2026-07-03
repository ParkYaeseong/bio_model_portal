# RAPID Langflow Workflow MVP — Design (SP1)

Date: 2026-07-03
Status: Approved (design)
Scope: **SP1 only.** SP2 (portal MCP server), SP3 (multi-provider execution chatbot),
SP4 (self-improvement loop) are separate specs, sequenced after SP1.

## 1. Goal

Let a Bio Model Portal user instantiate and run a fixed **RAPID protein-design
workflow** from the portal UI, edit each step's parameters, execute it as a
tracked multi-step async job, and view per-step status / logs / metrics and a
final candidate report.

RAPID flow:

```
FASTA/PDB Input → MSA Search → Conservation Mask → ProteinMPNN Design
  → SoluProt Filter → Structure Validation → Report Export
```

## 2. Key decisions (from brainstorming)

- **Template-run MVP.** No free-form visual editing in the MVP. Langflow is used
  only to *define* the RAPID DAG schema and is **not deployed in the MVP**; the
  embedded Langflow builder is Phase 2 and will import/export the same DAG schema.
- **Portal-orchestrated.** The portal backend runs the DAG step-by-step using the
  existing async job infrastructure (gateway + `Job` + `JobMonitor`). Langflow is
  not in the runtime execution path.
- **Steps reuse existing portal Jobs.** Each `WorkflowRunStep` that needs a GPU
  worker creates a normal portal `Job` (e.g. `pipeline=proteinmpnn`) via the same
  code path as the UI, inheriting gateway submission, `JobMonitor` polling, and
  artifact storage. No new worker services or URLs.

### Non-goals (MVP)

- No Langflow deployment, no visual node editing, no custom Langflow components.
- No `Project` entity (portal has none today); `project_id` is a nullable column
  reserved for later, unused in the MVP.
- No real SoluProt worker (none exists in the gateway) — MVP uses a mock scorer
  behind a real-shaped interface.
- No chatbot / MCP execution (SP2/SP3).

## 3. Architecture

### 3.1 Reality baseline (existing, reused)

- Production is **systemd** (`bmp-backend` 18121 / `bmp-frontend` 18120 /
  `bmp-gateway` 18122 / `bmp-sso`), not docker-compose. `portal/docker-compose.yml`
  is a 2-service dev convenience and is **not** modified.
- GPU workers are remote HTTP at `211.188.35.221:181xx`, fronted by the portal
  **gateway** (RunPod-compat `/v2/{endpoint}/run` + `/status`). Existing gateway
  endpoints: `proteinmpnn`, `mmseqs`, `esmfold`, `colabfold`, `alphafold`,
  `rosetta_relax`, `bioemu`, `rfdiffusion`, `diffdock`, `phastest`.
- `backend/app/tasks.py::JobMonitor` is a daemon thread that polls `Job`s in
  active states and updates status + artifacts from the gateway.
- Models today: `User`, `Job`, `Artifact` only.

### 3.2 New execution model

```
WorkflowRun (status machine)
  └─ WorkflowRunStep[order]  ──creates──> Job (existing pipeline job)
                                            └─ JobMonitor polls to terminal
  WorkflowMonitor (new daemon thread, mirrors JobMonitor):
    for each running WorkflowRun:
      look at current step's Job status
      on Job terminal success -> map outputs to next step inputs -> create next Job
      on Job terminal failure -> mark step+run failed, stop
      when last step done -> run completed, build output_summary
```

- Portal-side steps (Input, Conservation Mask, SoluProt mock, Report) execute
  **inline** inside `WorkflowMonitor` (fast, no GPU job); worker steps create a
  `Job` and yield until it finishes.
- Data handoff: each step writes result files under the run's storage dir; the
  DAG template declares, per edge, which upstream artifact feeds which downstream
  input. `result_path` + `metrics` are always persisted on the step.

## 4. Data model (new SQLAlchemy models in `models.py`)

**Workflow**
- `id` (uuid str, pk), `name`, `description`, `owner_id` (fk users),
  `template_key` (str, e.g. `rapid_v1`), `dag` (JSON), `langflow_flow_id`
  (str, nullable — Phase 2), `created_at`, `updated_at`.

**WorkflowRun**
- `id` (uuid pk), `workflow_id` (fk), `owner_id` (fk), `project_id` (nullable,
  unused MVP), `status` (`queued|running|completed|failed|cancelled`),
  `input_summary` (JSON), `output_summary` (JSON), `created_at`, `started_at`,
  `finished_at`, `error_message` (text, nullable).

**WorkflowRunStep**
- `id` (uuid pk), `run_id` (fk), `order` (int), `step_name`, `worker_name`,
  `status` (`queued|running|completed|failed|cancelled|skipped`),
  `job_id` (str nullable → portal `Job.id`), `parameters` (JSON),
  `result_path` (str nullable), `metrics` (JSON), `logs` (text),
  `error_message` (text nullable), `created_at`, `finished_at`.

Tables are created via the existing `Base.metadata.create_all` path used at
startup (SQLite). Cascade delete run→steps.

## 5. RAPID DAG template (`workflow/templates/rapid_v1.json`)

Langflow-compatible shape (nodes + edges) so Phase 2 can round-trip:

```json
{
  "template_key": "rapid_v1",
  "nodes": [
    {"id": "input",        "step_name": "FASTA/PDB Input",       "worker": "portal.input",        "params": {}},
    {"id": "msa",          "step_name": "MSA Search",            "worker": "gateway.mmseqs",      "params": {"max_seqs": ""}},
    {"id": "conservation", "step_name": "Conservation Mask",     "worker": "portal.conservation", "params": {"tiers": [30, 50, 70]}},
    {"id": "mpnn",         "step_name": "ProteinMPNN Design",    "worker": "gateway.proteinmpnn", "params": {"num_seq_per_target": 16, "sampling_temp": 0.1, "seed": 0, "batch_size": 1}},
    {"id": "soluprot",     "step_name": "SoluProt Filter",       "worker": "portal.soluprot_mock","params": {"top_k": 20}},
    {"id": "validate",     "step_name": "Structure Validation",  "worker": "gateway.esmfold",     "params": {"plddt_cutoff": 85, "rmsd_cutoff": 2.0}},
    {"id": "report",       "step_name": "Report Export",         "worker": "portal.report",       "params": {}}
  ],
  "edges": [
    {"from": "input", "to": "msa",          "map": {"sequence": "sequence"}},
    {"from": "msa",   "to": "conservation", "map": {"msa": "msa"}},
    {"from": "input", "to": "mpnn",         "map": {"pdb": "backbone"}},
    {"from": "conservation", "to": "mpnn",  "map": {"mask": "fixed_positions"}},
    {"from": "mpnn",  "to": "soluprot",     "map": {"fasta": "candidates"}},
    {"from": "soluprot", "to": "validate",  "map": {"top_candidates": "sequences"}},
    {"from": "validate", "to": "report",    "map": {"structures": "structures", "metrics": "metrics"}}
  ]
}
```

### Step → worker mapping (MVP real vs mock)

| Step | Worker | MVP |
|---|---|---|
| FASTA/PDB Input | portal (no worker) | real |
| MSA Search | gateway `mmseqs` | real |
| Conservation Mask (tiers 30/50/70) | portal compute from MSA | real (simple per-column identity conservation) |
| **ProteinMPNN Design** | gateway `proteinmpnn` | **real** |
| SoluProt Filter (top_k 20) | **no worker** | **mock scorer**, real-shaped interface |
| Structure Validation | gateway `esmfold` → pLDDT; RMSD vs input PDB | real (swappable to colabfold) |
| Report Export | portal | real |

MVP defaults: ProteinMPNN `num_seq_per_target=16, sampling_temp=0.1, seed=0,
batch_size=1`; validation `pLDDT>=85, RMSD<=2.0, top_k=20`; conservation tiers
`30,50,70`.

## 6. Backend modules

- `backend/app/workflow/orchestrator.py` — build step inputs from upstream
  artifacts + params; create the per-step portal `Job`; portal-side step execs
  (conservation, soluprot_mock, report); output_summary assembly.
- `backend/app/workflow/monitor.py` — `WorkflowMonitor` daemon thread (mirrors
  `JobMonitor`): advances running runs on step-job completion; started in
  `main.py` lifespan alongside `JobMonitor`.
- `backend/app/workflow/conservation.py` — MSA → per-column conservation →
  tiered fixed-position mask.
- `backend/app/workflow/soluprot_mock.py` — deterministic mock solubility score
  (seeded by sequence hash) behind the same signature a real SoluProt client
  would use; top_k selection.
- `backend/app/workflow/templates/rapid_v1.json` — the DAG above.
- `backend/app/routers/workflows.py` — REST API (below).

### 6.1 API routes (`/api/workflows`)

- `GET  /api/workflows` — list workflows for the user (name, desc, last run status, created).
- `POST /api/workflows` — instantiate from `template_key` (creates a Workflow row from the template).
- `GET  /api/workflows/{id}` — workflow detail + its runs.
- `POST /api/workflows/{id}/runs` — start a run with per-step params + inputs (sequence/PDB upload); returns run id.
- `GET  /api/workflows/runs/{run_id}` — run status + steps (status/logs/metrics).
- `GET  /api/workflows/runs/{run_id}/report` — candidate table + downloadable artifacts.
- `POST /api/workflows/runs/{run_id}/cancel` — cancel (mark cancelled; cancel active step job).

All gated by existing `get_current_user` SSO dependency; ownership enforced.

## 7. Frontend (Next.js, existing patterns)

New route group `frontend/src/app/workflows/`:
- **List** — workflows with last-run status badge + `실행` / `상세`.
- **Create** — instantiate RAPID template; per-step parameter form pre-filled
  from template defaults; input upload (FASTA/PDB); `실행`.
  (A disabled `Langflow에서 편집 (Phase 2)` placeholder button.)
- **Run detail** — overall status; per-step cards (status, logs, metrics:
  pLDDT/RMSD/SoluProt score); failed step visually flagged.
- **Report** — candidate sequence table (seq, SoluProt score, pLDDT, RMSD) +
  download buttons.

`frontend/src/lib/api.ts` — Workflow/Run/Step types + fetchers, reusing the
existing `apiFetch` + auth token pattern.

Portal home gets a `워크플로우` entry linking to the list (matches existing nav).

## 8. Error handling, logging, self-improvement hooks

- Any step job terminal-failure → step `failed` with `error_message` + captured
  `logs`; run → `failed`; downstream steps `skipped`; UI highlights the failed step.
- `result_path` and `metrics` persisted on every step (success or fail).
- All runs/steps/params/results are structured records → these ARE the SP4
  self-improvement corpus. SP1 only guarantees the data is captured; SP4 later
  adds feedback capture + human-reviewed template/param suggestions. No
  autonomous self-modification in any of SP1–SP3.

## 9. Config (`.env`)

- No new secrets for the MVP (workers reached via existing gateway config).
- Reserve `LANGFLOW_URL=` (empty, Phase 2). No hardcoded absolute paths; storage
  under the existing storage root; worker routing via existing `endpoints.yaml`.

## 10. Testing & rollout

1. Unit: orchestrator DAG advancement (ready-step selection, output→input
   mapping) and failure propagation, with a fake job layer.
2. Mock E2E: all steps mocked (including gateway) → a run reaches `completed`
   with a report.
3. Real ProteinMPNN: wire `proteinmpnn` to the real gateway; run one E2E from
   the UI (Playwright) with a small backbone; verify designs → mock SoluProt →
   ESMFold validation → report.
4. README section: architecture, how to run a RAPID workflow, how steps map to
   workers, and the Phase 2 (Langflow) direction.

## 11. Deliverables

- `backend/app/models.py` (+3 models), `backend/app/workflow/*`,
  `backend/app/routers/workflows.py`, `routers/__init__.py` + `main.py` wiring.
- `frontend/src/app/workflows/*`, `frontend/src/lib/api.ts` additions.
- `workflow/templates/rapid_v1.json`.
- README workflow section.
- Tests (orchestrator unit + mock E2E).

## 12. Phase 2 preview (not built here)

Deploy Langflow (systemd, Caddy `/langflow` behind SSO), custom components that
emit the `rapid_v1` node schema, embedded builder in the portal, import/export
between Langflow flows and the portal DAG. SP2/SP3/SP4 layer on top using the
run/step records SP1 produces.
