# Gateway → GPU-host model smoke tests

Date: 2026-06-15
GPU host (shared with RAPID prod): `211.188.35.221`
Gateway: `127.0.0.1:18122` (RunPod-v2-compatible)

All jobs kept minimal (10-residue sequences, a 3-residue toy PDB, `length=10`,
`num_samples=1`, `samples_per_complex=1`) to avoid consuming the shared GPUs.

## Gateway RunPod-v2 API surface (from `gateway/gateway.py`)

- Submit: `POST /v2/{endpoint_id}/run` with body `{"input": {...}}` → returns
  `{"id": "<job_id>", "status": "IN_QUEUE"}`. A 400 is returned if the body is
  not `{"input": {...}}`.
- Poll: `GET /v2/{endpoint_id}/status/{job_id}` → `{"id", "status", "output"?, "error"?}`.
  Status values: `IN_QUEUE` → `IN_PROGRESS` → `COMPLETED` | `FAILED`.
- Cancel: `POST /v2/{endpoint_id}/cancel/{job_id}` (forwards to worker `/cancel`,
  marks job `FAILED` "cancelled by user" unless already terminal).
- Health: `GET /v2/{endpoint_id}/health` (proxies worker `/healthz`), `GET /health`.

Internally the gateway, per endpoint (`gateway/endpoints.yaml`):
1. runs the configured **adapter** (`gateway/adapters.py`) to reshape `input`,
2. POSTs `{"input": <adapted>, "id": <job_id>}` to `{worker_url}/run`,
3. runs the **packager** (`gateway/packagers.py`) over the worker `output`,
   returning `output.archives[0].base64` (a tar.gz) plus `stdout`/`stderr`/`raw`.

## Results

| model | endpoint | result | notes |
|---|---|---|---|
| esmfold | esmfold-local | **PASS** | No adapter; `{"sequence":"MKTAYIAKQR"}`. Completed ~54s. Worker output had `pdb`, `ranked_0_pdb`, `best_plddt`; esmfold packager built archive (b64 len 6552). |
| proteinmpnn | proteinmpnn-local | **PASS** | Adapter `proteinmpnn` extracted `.pdb` from `input_archive` → `pdb_base64`+`pdb_name`. Completed ~3s. Worker returned `native`/`samples`/`raw_fasta`; stdout "Using ProteinMPNN trained on soluble proteins only!". |
| mmseqs | mmseqs-local | **PASS** | Adapter `mmseqs` turned `{"sequence":...}` into `query_fasta`+`task=search`+`target_db=uniref90`. Completed ~42s on `uniref90_gpu`. Output `tsv`/`a3m_gz_b64`/`hit_count`/`query_id`. |
| rfdiffusion | rfdiffusion-local | **PASS** | Adapter `rfdiffusion`: unconditional `{"length":10}` → `inputs={"spec-1":{"length":"10"}}`. Completed ~24s. Output `selected`/`designs`/`backend` (rfd3). |
| rosetta-relax | rosetta-relax-local | **PASS** | Adapter `rosetta_relax` extracted `.pdb` from `input_archive` → `pdb_content`+`target_id`. Completed ~4s. Output `relaxed_pdb_content`/`total_score`/`scorefile`. |
| bioemu | bioemu-local | **PASS** | No adapter; `{"sequence":"MKTAYIAKQR","num_samples":1,...}` (worker accepts bare sequence; gateway forwards as-is). Reached IN_PROGRESS with no schema error and completed quickly for this tiny input. |
| colabfold | colabfold-local | **ACCEPTED** | No adapter; `{"sequence":"MKTAYIAKQR"}`. Reached IN_PROGRESS with no schema/adapter error; cancelled to spare shared GPU (heavy MSA+AF2 run). LB upstreams all healthy. |
| diffdock | diffdock-local | **ACCEPTED** | No adapter; passthrough payload `{cmd, protein_ligand_csv, pdb_files, sdf_files, data_dir, inputs_dir, out_dir, config}` (SMILES ligand `CCO`, toy PDB). Reached IN_PROGRESS with no schema error; cancelled to spare shared GPU. |

## Verdict

DONE. All 8 endpoints exercised through the gateway's adapter + packager path.
6 ran to completion with sane output; colabfold and diffdock were confirmed
ACCEPTED (reached IN_PROGRESS, no schema/adapter error) and cancelled to avoid
consuming the shared production GPUs. No adapter or schema mismatches found.

### Input-shape reference (what each worker expects, source of truth)

Confirmed against `protein_pipeline` RunPod clients
(`pipeline-mcp/src/pipeline_mcp/clients/*`) and the esmfold2 worker:

- esmfold / colabfold / bioemu: `sequence` (bioemu also `num_samples`, return flags).
- proteinmpnn: `pdb_base64`+`pdb_name` (adapter derives from `input_archive`/`pdb_*`).
- mmseqs: `task`+`query_fasta`+`target_db` (adapter derives from `sequence`).
- rfdiffusion (rfd3): `inputs`/`input_files` (adapter derives from `length`/`contigs`/PDB).
- rosetta-relax: `pdb_content`+`target_id`+`nstruct` (adapter derives from `input_archive`).
- diffdock: `cmd`+`protein_ligand_csv`+`pdb_files`+`sdf_files`+dir fields (passthrough).

## 2026-06-16 vendored-prep verification

Final E2E verification after vendoring `protein_pipeline` input-prep into the
gateway and rewriting per-model adapters to use it. Gateway unit suite:
**27 passed**. Services `bmp-gateway`/`bmp-backend`/`bmp-frontend` all active;
`/health` lists all 10 endpoints; every endpoint reports `worker_status:200`.

| model | endpoint | result |
|---|---|---|
| proteinmpnn | proteinmpnn-local | **COMPLETED** |
| rosetta-relax | rosetta-relax-local | **COMPLETED** |
| bioemu | bioemu-local | **COMPLETED** |
| mmseqs | mmseqs-local | **COMPLETED** |
| diffdock | diffdock-local | **IN_PROGRESS** (no error) |
| rfdiffusion (4KL5) | rfdiffusion-local | **COMPLETED** |
| esmfold | esmfold-local | health 200 |
| colabfold | colabfold-local | health 200 |
| alphafold | alphafold-local | health 200 |
| phastest | phastest-local | health 200 |

**4KL5 RFD3 regression FIXED.** The negative-residue contig case now runs the
full portal stack end-to-end (frontend proxy -> backend -> gateway -> worker):
bootstrap-login token -> backend `/api/rfdiffusion/contig-suggestions` returns a
recommended `ligand_motif` contig -> `rfd3.build_input` with
`contig_processed_coords:true` -> gateway `/run` -> job **COMPLETED**. No
"Invalid contig format" crash (no CONTIG-CRASH).
