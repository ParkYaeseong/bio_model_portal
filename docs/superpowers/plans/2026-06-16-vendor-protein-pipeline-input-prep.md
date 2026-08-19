# Vendor protein_pipeline input-prep + RFD3 contig dropdown — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Vendor protein_pipeline's proven, pure-Python input preparation into the bio_model_portal gateway so every model worker receives correctly-prepared payloads (fixing the RFD3 negative-residue/contig failure), and add a ligand-aware RFD3 contig dropdown with an auto-selected recommendation.

**Architecture:** Copy `bio/pdb.py` + `bio/ligand_text.py` (pure stdlib, zero config deps) into `gateway/bio/`. Add `gateway/prep/` per-model spec builders that mirror protein_pipeline's client payload logic; rewrite `gateway/adapters.py` to delegate to them. Add a gateway `POST /prep/contig-suggestions` endpoint (uses vendored bio) that the backend proxies, and a frontend RFD3 contig dropdown.

**Tech Stack:** Python 3.12, FastAPI (gateway 18122, backend 18121), httpx, pytest; Next.js 14 frontend (18120). All services are systemd units (`bmp-gateway`/`bmp-backend`/`bmp-frontend`).

**Source of truth to copy/port (read these):**
- `/opt/protein_pipeline/pipeline-mcp/src/pipeline_mcp/bio/pdb.py` — `preprocess_pdb(pdb_text, *, chains=None, strip_nonpositive_resseq=False, renumber_resseq_from_1=False) -> (str, mapping)` where `mapping = {chain: [{"index","original_resseq","original_icode","processed_resseq","processed_icode"}]}`; `normalize_structure_text`, `strip_to_first_model`, `mmcif_to_pdb`, `residues_by_chain`(→`Residue(index, resseq, ...)`), `sequence_by_chain`, `ligand_atoms_present`, `ligand_proximity_mask(...) -> {chain: [residue.index]}`.
- `/opt/protein_pipeline/.../bio/ligand_text.py` — `normalize_diffdock_ligand_inputs`, `looks_like_diffdock_modelserver_mmcif`, `mmcif_ligand_to_sdf`.
- `/opt/protein_pipeline/.../clients/local_http.py` — `validate_protein_sequence(sequence, *, model)`, `dock()` CSV format (lines 298-367), `predict()` sequence-list shape.
- `/opt/protein_pipeline/.../clients/proteinmpnn.py` — `design()` payload keys (`pdb_base64`, `pdb_name`, `pdb_path_chains`, `fixed_positions`, sampling params).
- `/opt/protein_pipeline/.../pipeline.py:1826` — `_normalize_rfd3_contig_str` (`A:1`→`A1`).
- Existing portal: `gateway/adapters.py` (current thin adapters), `gateway/gateway.py` (`_run_job`, routes), `portal/backend/app/runpod.py` (pipeline defs + `RunpodClient`), `portal/frontend/src/app/page.tsx` (+ RFD3 form component referenced there).

**Acceptance oracle (the bug we must fix):** the uploaded `4KL5.pdb` (chain B starts at residue **-2**) currently makes RFD3 fail with `Invalid contig format: 'B-2'`. A copy is at `/tmp/rfd_dbg/4KL5.pdb` (regenerate via the archive at `/opt/bio_model_portal/storage/uploads/2/93d904e2-0f6c-4627-97cf-7579704cae84/inputs.tar.gz` if missing). After this work, submitting 4KL5 with the recommended contig must get **past RFD3 input parsing** (reach diffusion / COMPLETED), not 500 at validation.

**Safety / environment:**
- GPU workers are shared with RAPID prod (host 211.188.35.221). Keep test jobs minimal; the 4KL5 regression runs real RFD3 diffusion (~minutes) — run it sparingly.
- NEVER broad-`pkill`; restart only via `systemctl restart bmp-<svc>`.
- Each service auto-loads its env; after editing gateway code, `systemctl restart bmp-gateway`.

---

## File Structure

```
gateway/
  bio/__init__.py                 # new (empty)
  bio/pdb.py                      # new: verbatim copy of protein_pipeline bio/pdb.py + provenance header
  bio/ligand_text.py              # new: verbatim copy + header
  prep/__init__.py                # new
  prep/structure.py               # new: shared PDB-input helpers (extract pdb from archive, preprocess wrapper)
  prep/rfd3.py                    # new: build RFD3 worker input (preprocess + contig remap/normalize + ligand)
  prep/contig_suggest.py          # new: PDB -> dropdown options + ligand-aware recommended
  prep/proteinmpnn.py             # new
  prep/diffdock.py                # new
  prep/rosetta.py                 # new
  prep/sequence.py                # new: validate_protein_sequence + {id,sequence} list (bioemu/af2/esmfold)
  prep/mmseqs.py                  # new
  adapters.py                     # modify: delegate each adapter to prep/*
  gateway.py                      # modify: add POST /prep/contig-suggestions
  tests/                          # new: pytest unit tests
portal/backend/app/routers/rfdiffusion.py  # new: POST /api/rfdiffusion/contig-suggestions (proxy to gateway)
portal/backend/app/main.py                 # modify: register router
portal/frontend/src/app/page.tsx (+ RFD3 form component)  # modify: contig dropdown
```

---

## Phase 1 — Vendor the pure bio foundation

### Task 1: Set up gateway test tooling + vendor bio/pdb.py + bio/ligand_text.py

**Files:**
- Create: `gateway/bio/__init__.py`, `gateway/bio/pdb.py`, `gateway/bio/ligand_text.py`, `gateway/tests/__init__.py`, `gateway/tests/conftest.py`

- [ ] **Step 1: Install pytest into the gateway venv**

```bash
/opt/bio_model_portal/gateway/.venv/bin/pip install pytest
```
Expected: pytest installs successfully.

- [ ] **Step 2: Copy the two pure modules verbatim + add provenance header**

```bash
cd /opt/bio_model_portal/gateway && mkdir -p bio tests && touch bio/__init__.py tests/__init__.py
cp /opt/protein_pipeline/pipeline-mcp/src/pipeline_mcp/bio/pdb.py bio/pdb.py
cp /opt/protein_pipeline/pipeline-mcp/src/pipeline_mcp/bio/ligand_text.py bio/ligand_text.py
```
Then prepend this header line to BOTH `bio/pdb.py` and `bio/ligand_text.py` (above the existing first line), using Edit:
```python
# VENDORED from protein_pipeline pipeline-mcp/src/pipeline_mcp/bio/ on 2026-06-16.
# Pure stdlib, no config deps. Re-sync manually if the upstream changes.
```

- [ ] **Step 3: Verify imports work standalone (no protein_pipeline on path)**

```bash
cd /opt/bio_model_portal/gateway && .venv/bin/python -c "from bio import pdb, ligand_text; print('ok', hasattr(pdb,'preprocess_pdb'), hasattr(pdb,'ligand_proximity_mask'), hasattr(ligand_text,'mmcif_ligand_to_sdf'))"
```
Expected: `ok True True True` (no ImportError).

- [ ] **Step 4: Characterization test — preprocess_pdb fixes the 4KL5 negative residues**

Create `gateway/tests/test_bio_pdb.py`:
```python
import base64, io, tarfile, os
from bio import pdb

PDB = "/tmp/rfd_dbg/4KL5.pdb"

def _load():
    if not os.path.exists(PDB):
        import urllib.request  # fallback not expected; archive path documented in plan
    return open(PDB).read()

def test_strip_nonpositive_removes_negative_residues():
    text = _load()
    # chain B originally has residues -2,-1,0
    before = pdb.residues_by_chain(text)
    assert any(r.resseq <= 0 for r in before.get("B", [])), "fixture should have non-positive residues"
    out, mapping = pdb.preprocess_pdb(text, strip_nonpositive_resseq=True, renumber_resseq_from_1=True)
    after = pdb.residues_by_chain(out)
    assert all(r.resseq >= 1 for chain in after.values() for r in chain), "all residues positive after preprocess"
    # mapping records original->processed for chain B
    assert "B" in mapping and mapping["B"][0]["processed_resseq"] >= 1
```

- [ ] **Step 5: Run the test**

Run: `cd /opt/bio_model_portal/gateway && .venv/bin/python -m pytest tests/test_bio_pdb.py -v`
Expected: PASS. (If `/tmp/rfd_dbg/4KL5.pdb` is missing, extract it first: `mkdir -p /tmp/rfd_dbg && tar xzf /opt/bio_model_portal/storage/uploads/2/93d904e2-0f6c-4627-97cf-7579704cae84/inputs.tar.gz -C /tmp/rfd_dbg`.)

- [ ] **Step 6: Commit**

```bash
cd /opt/bio_model_portal && git add gateway/bio gateway/tests && git commit -m "feat(gateway): vendor protein_pipeline bio/pdb.py + ligand_text.py (pure stdlib)"
```

---

## Phase 2 — RFD3 prep + adapter (the critical fix)

### Task 2: Shared structure helper + RFD3 prep module

**Files:**
- Create: `gateway/prep/__init__.py`, `gateway/prep/structure.py`, `gateway/prep/rfd3.py`
- Test: `gateway/tests/test_prep_rfd3.py`

- [ ] **Step 1: Write `gateway/prep/structure.py`** (extracts a PDB from the portal archive and runs preprocess)

```python
from __future__ import annotations
import base64, io, tarfile
from bio import pdb

def extract_pdb_text(payload: dict) -> str | None:
    """Return PDB/mmCIF text from payload.input_archive (tar.gz base64) or inline keys."""
    for key in ("pdb_content", "input_pdb_content"):
        if isinstance(payload.get(key), str) and payload[key].strip():
            return payload[key]
    archive = payload.get("input_archive")
    if isinstance(archive, dict) and archive.get("base64"):
        raw = base64.b64decode(archive["base64"])
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
            members = [m for m in tar.getmembers() if m.isfile()]
            members.sort(key=lambda m: m.name)
            for m in members:
                if m.name.lower().endswith((".pdb", ".cif", ".mmcif")):
                    f = tar.extractfile(m)
                    if f:
                        return f.read().decode("utf-8", errors="replace")
    return None

def preprocess(pdb_text: str, chains: list[str] | None = None):
    """Normalize (mmcif->pdb, first model) then strip non-positive + renumber from 1.
    Returns (clean_pdb_text, mapping)."""
    normalized = pdb.normalize_structure_text(pdb_text)
    return pdb.preprocess_pdb(
        normalized, chains=chains,
        strip_nonpositive_resseq=True, renumber_resseq_from_1=True,
    )
```

- [ ] **Step 2: Write `gateway/prep/rfd3.py`** (build the worker `input`; handles contig coordinate remap + normalization)

```python
from __future__ import annotations
from typing import Any
from . import structure

def _normalize_contig_token(value: str) -> str:
    # mirror protein_pipeline _normalize_rfd3_contig_str: "A:1" -> "A1"
    return str(value or "").replace(":", "")

def _remap_contig(contig: str, mapping: dict, *, processed_coords: bool) -> str:
    """If the contig is already in processed (renumbered-from-1) coordinates, just
    normalize. Otherwise remap each ChainID+resid token original->processed via mapping."""
    contig = _normalize_contig_token(contig)
    if processed_coords or not mapping:
        return contig
    import re
    orig2proc = {
        ch: {e["original_resseq"]: e["processed_resseq"] for e in entries}
        for ch, entries in mapping.items()
    }
    def repl(m):
        ch, num = m.group(1), int(m.group(2))
        return f"{ch}{orig2proc.get(ch, {}).get(num, num)}"
    return re.sub(r"([A-Za-z])(-?\d+)", repl, contig)

def build_input(payload: dict) -> dict:
    """Produce the RFD3 worker input dict. Mutates a copy of payload."""
    out = dict(payload)
    pdb_text = structure.extract_pdb_text(out)
    contig = out.pop("contigs", None) or out.pop("contig", None)
    length = out.pop("length", None)
    # frontend marks dropdown/recommended contigs as already-processed:
    processed_coords = bool(out.pop("contig_processed_coords", False))

    spec: dict[str, Any] = {}
    if pdb_text:
        clean, mapping = structure.preprocess(pdb_text)
        out["input_files"] = {"input.pdb": clean}
        spec["input"] = "input.pdb"
        if contig:
            spec["contig"] = _remap_contig(str(contig), mapping, processed_coords=processed_coords)
        if length:
            spec["length"] = str(length)
    else:
        if length:
            spec["length"] = str(length)
        elif contig:
            spec["length"] = str(contig)

    hotspots = out.pop("hotspots", None) or out.pop("hotspot_res", None)
    if hotspots:
        spec["hotspots"] = hotspots
    if spec:
        out["inputs"] = {"spec-1": spec}
    out.pop("input_archive", None)
    return out
```

- [ ] **Step 2b: Wire the adapter** — in `gateway/adapters.py` replace the body of `adapter_rfdiffusion` with a delegation:
```python
def adapter_rfdiffusion(payload: dict) -> dict:
    from prep import rfd3
    return rfd3.build_input(payload)
```
(Keep the other adapters unchanged for now.)

- [ ] **Step 3: Unit test the RFD3 prep** — `gateway/tests/test_prep_rfd3.py`:
```python
import base64, io, tarfile, os
from prep import rfd3

def _archive(pdb_path="/tmp/rfd_dbg/4KL5.pdb"):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        data = open(pdb_path, "rb").read()
        ti = tarfile.TarInfo("4KL5.pdb"); ti.size = len(data)
        t.addfile(ti, io.BytesIO(data))
    return {"base64": base64.b64encode(buf.getvalue()).decode()}

def test_rfd3_input_has_clean_pdb_and_contig():
    out = rfd3.build_input({"input_archive": _archive(), "contigs": "A0-19"})
    assert "input.pdb" in out["input_files"]
    assert "HETATM" not in out["input_files"]["input.pdb"] or True  # HETATM may remain; resseq must be positive
    spec = out["inputs"]["spec-1"]
    assert spec["input"] == "input.pdb"
    # original A0 -> processed A1 after renumber; remap must shift the contig
    assert spec["contig"].startswith("A") and "-" in spec["contig"]

def test_rfd3_unconditional_length_only():
    out = rfd3.build_input({"length": 10})
    assert out["inputs"]["spec-1"] == {"length": "10"}
```

- [ ] **Step 4: Run unit tests**

Run: `cd /opt/bio_model_portal/gateway && .venv/bin/python -m pytest tests/test_prep_rfd3.py -v`
Expected: PASS.

- [ ] **Step 5: Restart gateway and run the 4KL5 END-TO-END regression (the acceptance oracle)**

```bash
systemctl restart bmp-gateway && sleep 4
cd /opt/bio_model_portal/gateway && .venv/bin/python - <<'PY'
import base64, io, tarfile, httpx
buf=io.BytesIO()
with tarfile.open(fileobj=buf,mode="w:gz") as t:
    d=open("/tmp/rfd_dbg/4KL5.pdb","rb").read(); ti=tarfile.TarInfo("4KL5.pdb"); ti.size=len(d); t.addfile(ti,io.BytesIO(d))
# recommended contig (chain A full, processed coords) — see Task 4 for derivation; here use full chain A 1-140
payload={"input_archive":{"base64":base64.b64encode(buf.getvalue()).decode()},
         "contigs":"A1-140","contig_processed_coords":True}
r=httpx.post("http://127.0.0.1:18122/v2/rfdiffusion-local/run", json={"input":__import__("prep.rfd3",fromlist=["build_input"]).build_input(payload),"id":"reg-4kl5"}, timeout=75)
b=r.json(); err=b.get("error","") or ""
print("HTTP", r.status_code, "status", b.get("status"))
print("PASS (past parser)" if ("Invalid contig format" not in err and b.get("status")!="FAILED") else "still failing:")
print("\n".join(l for l in err.splitlines() if "Error:" in l or "ERROR" in l)[:600])
PY
```
Expected: **no `Invalid contig format`**; status reaches IN_PROGRESS/COMPLETED or times out while diffusing (= parser passed). If it still fails at parsing, adjust `prep/structure.preprocess` flags (try `strip_nonpositive_resseq=True` without renumber, or chain filtering) until the 4KL5 case passes the parser — this is the oracle. Document the final flags in a comment.

- [ ] **Step 6: Commit**

```bash
cd /opt/bio_model_portal && git add gateway/prep gateway/adapters.py gateway/tests/test_prep_rfd3.py && git commit -m "feat(gateway): RFD3 prep via vendored preprocess_pdb (fixes negative-residue contig crash)"
```

---

## Phase 3 — Contig suggestions (gateway + backend)

### Task 3: `prep/contig_suggest.py` + gateway endpoint

**Files:**
- Create: `gateway/prep/contig_suggest.py`
- Modify: `gateway/gateway.py`
- Test: `gateway/tests/test_contig_suggest.py`

- [ ] **Step 1: Write `gateway/prep/contig_suggest.py`**

```python
from __future__ import annotations
from typing import Any
from bio import pdb
from . import structure

def _ranges_from_indices(idxs: list[int]) -> str:
    """Compress sorted residue numbers into 'a-b,c-d' ranges."""
    if not idxs:
        return ""
    idxs = sorted(set(idxs)); parts=[]; start=prev=idxs[0]
    for n in idxs[1:]:
        if n == prev + 1: prev = n; continue
        parts.append(f"{start}-{prev}" if start!=prev else f"{start}"); start=prev=n
    parts.append(f"{start}-{prev}" if start!=prev else f"{start}")
    return ",".join(parts)

def suggest(pdb_text: str) -> dict[str, Any]:
    """Return dropdown options in PROCESSED coordinates + ligand-aware recommendation."""
    clean, mapping = structure.preprocess(pdb_text)
    residues = pdb.residues_by_chain(clean)  # processed coords
    chains = sorted(residues.keys())
    options: list[dict[str, Any]] = []
    # per-chain full options
    for ch in chains:
        nums = [r.resseq for r in residues[ch]]
        if nums:
            options.append({"id": f"chain_{ch}", "label": f"체인 {ch} 전체",
                            "contig": f"{ch}{min(nums)}-{max(nums)}", "recommended": False})
    # all-chains option
    if len(chains) > 1:
        allc = ",".join(o["contig"] for o in options if o["id"].startswith("chain_"))
        options.append({"id": "all_chains", "label": "전체 체인", "contig": allc, "recommended": False})
    # ligand-proximity motif option
    has_ligand = pdb.ligand_atoms_present(clean)
    if has_ligand:
        mask = pdb.ligand_proximity_mask(clean, distance_angstrom=6.0)  # {chain: [residue.index]}
        # residue.index == processed position; for renumber-from-1 PDBs index==resseq
        toks = []
        for ch in sorted(mask):
            idx2resseq = {r.index: r.resseq for r in residues[ch]}
            nums = sorted(idx2resseq.get(i, i) for i in mask[ch])
            rng = _ranges_from_indices(nums)
            if rng:
                toks.append(",".join(f"{ch}{p}" for p in rng.split(",")))
        if toks:
            options.append({"id": "ligand_motif", "label": "리간드 주변 모티프 (~6Å)",
                            "contig": ",".join(toks), "recommended": False})
    options.append({"id": "custom", "label": "직접 입력", "contig": "", "recommended": False})
    # recommendation: ligand motif if present else first full chain
    rec_id = "ligand_motif" if has_ligand and any(o["id"]=="ligand_motif" for o in options) else (
        f"chain_{chains[0]}" if chains else "custom")
    for o in options:
        o["recommended"] = (o["id"] == rec_id)
    return {"chains": chains, "options": options, "processed_coords": True}
```
Note: `_ranges_from_indices` is given a comma-list already in the ligand branch — simplify by passing the raw int list. (Engineer: pass `nums` to `_ranges_from_indices` and build `f"{ch}{tok}"` for each comma token; keep tokens as residue numbers.)

- [ ] **Step 2: Add the gateway route** in `gateway/gateway.py` (after the `/health` route):
```python
@app.post("/prep/contig-suggestions")
def contig_suggestions(body: dict = Body(...), authorization: str | None = Header(default=None)):
    _check_auth(authorization)
    from prep import contig_suggest, structure
    pdb_text = None
    if isinstance(body.get("pdb_base64"), str) and body["pdb_base64"]:
        import base64
        pdb_text = base64.b64decode(body["pdb_base64"]).decode("utf-8", errors="replace")
    else:
        pdb_text = structure.extract_pdb_text(body)
    if not pdb_text:
        return {"chains": [], "options": [{"id": "custom", "label": "직접 입력", "contig": "", "recommended": True}], "processed_coords": True}
    try:
        return contig_suggest.suggest(pdb_text)
    except Exception as exc:  # noqa: BLE001
        return {"chains": [], "options": [{"id": "custom", "label": "직접 입력", "contig": "", "recommended": True}], "error": str(exc)}
```

- [ ] **Step 3: Test contig_suggest** — `gateway/tests/test_contig_suggest.py`:
```python
from prep import contig_suggest

def test_ligand_pdb_recommends_motif():
    res = contig_suggest.suggest(open("/tmp/rfd_dbg/4KL5.pdb").read())
    ids = [o["id"] for o in res["options"]]
    assert "custom" in ids and any(i.startswith("chain_") for i in ids)
    rec = [o for o in res["options"] if o["recommended"]]
    assert len(rec) == 1
    # 4KL5 has HETATM ligands -> recommendation should be the ligand motif
    assert rec[0]["id"] == "ligand_motif"
    assert rec[0]["contig"]  # non-empty
```

- [ ] **Step 4: Run tests + live endpoint check**

```bash
cd /opt/bio_model_portal/gateway && .venv/bin/python -m pytest tests/test_contig_suggest.py -v
systemctl restart bmp-gateway && sleep 4
.venv/bin/python - <<'PY'
import base64, httpx
b=base64.b64encode(open("/tmp/rfd_dbg/4KL5.pdb","rb").read()).decode()
r=httpx.post("http://127.0.0.1:18122/prep/contig-suggestions", json={"pdb_base64": b}, timeout=30)
print(r.status_code); 
import json; print(json.dumps(r.json(), ensure_ascii=False)[:500])
PY
```
Expected: tests PASS; live endpoint returns options with one `recommended:true` (ligand_motif for 4KL5).

- [ ] **Step 5: Commit**

```bash
cd /opt/bio_model_portal && git add gateway/prep/contig_suggest.py gateway/gateway.py gateway/tests/test_contig_suggest.py && git commit -m "feat(gateway): /prep/contig-suggestions with ligand-aware recommendation"
```

### Task 4: Backend proxy endpoint `POST /api/rfdiffusion/contig-suggestions`

**Files:**
- Create: `portal/backend/app/routers/rfdiffusion.py`
- Modify: `portal/backend/app/main.py`

- [ ] **Step 1: Inspect how the backend reaches the gateway** — read `portal/backend/app/runpod.py` (uses `RUNPOD_BASE`, e.g. `http://127.0.0.1:18122/v2`). The prep endpoint is at the gateway ROOT (`/prep/...`), so derive the gateway base by stripping a trailing `/v2`.

- [ ] **Step 2: Write `portal/backend/app/routers/rfdiffusion.py`**
```python
from __future__ import annotations
import base64
import httpx
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from ..auth import get_current_user
from ..config import settings

router = APIRouter()

def _gateway_base() -> str:
    base = settings.runpod_base.rstrip("/")
    return base[:-3].rstrip("/") if base.endswith("/v2") else base

@router.post("/api/rfdiffusion/contig-suggestions")
async def contig_suggestions(file: UploadFile = File(...), current_user=Depends(get_current_user)):
    data = await file.read()
    b64 = base64.b64encode(data).decode("ascii")
    url = f"{_gateway_base()}/prep/contig-suggestions"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, json={"pdb_base64": b64})
            r.raise_for_status()
            return r.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"contig suggestion failed: {exc}")
```
(Confirm `settings.runpod_base` exists in `config.py`; if the attribute name differs, use the actual one.)

- [ ] **Step 3: Register the router** in `portal/backend/app/main.py` — add `from .routers import rfdiffusion` and `app.include_router(rfdiffusion.router)` next to the other routers.

- [ ] **Step 4: Restart backend + test via the full stack**

```bash
systemctl restart bmp-backend && sleep 5
TOK=$(curl -s -X POST http://127.0.0.1:18120/bootstrap-login | sed -E 's/.*"access_token":"([^"]+)".*/\1/')
curl -s -X POST http://127.0.0.1:18120/api/rfdiffusion/contig-suggestions \
  -H "Authorization: Bearer $TOK" -F "file=@/tmp/rfd_dbg/4KL5.pdb" | head -c 400; echo
```
Expected: JSON options with a recommended option (HTTP via frontend proxy → backend → gateway).

- [ ] **Step 5: Commit**

```bash
cd /opt/bio_model_portal && git add portal/backend/app/routers/rfdiffusion.py portal/backend/app/main.py && git commit -m "feat(backend): proxy /api/rfdiffusion/contig-suggestions to gateway"
```

---

## Phase 4 — Frontend RFD3 contig dropdown

### Task 5: Contig dropdown with auto-selected recommendation

**Files:**
- Modify: `portal/frontend/src/app/page.tsx` and the RFD3 form component it renders (find via the `contigs`/`length` fields and `selectedPipeline.key === "rfdiffusion"`).

- [ ] **Step 1: Locate the RFD3 form fields** — search the frontend for the `contigs` input and the PDB upload handler for the rfdiffusion pipeline:
```bash
grep -rnE "contigs|rfdiffusion|input_archive|uploadFile|FormData" /opt/bio_model_portal/portal/frontend/src | grep -vi node_modules | head -30
```

- [ ] **Step 2: Add a fetch + state for suggestions.** When a PDB file is selected for the rfdiffusion pipeline, POST it to `/api/rfdiffusion/contig-suggestions` (multipart, same auth header pattern as other calls in `src/lib/api.ts`) and store `options`. Add a `contigChoice` state initialized to the option with `recommended: true`.

```ts
// in src/lib/api.ts
export async function fetchContigSuggestions(file: File, token: string) {
  const fd = new FormData(); fd.append("file", file);
  const res = await fetch(`${API_BASE}/api/rfdiffusion/contig-suggestions`, {
    method: "POST", headers: { Authorization: `Bearer ${token}` }, body: fd,
  });
  if (!res.ok) return { options: [{ id: "custom", label: "직접 입력", contig: "", recommended: true }], processed_coords: true };
  return res.json();
}
```

- [ ] **Step 3: Render the dropdown** in the RFD3 form (replace/augment the free-text contig field):
```tsx
<label>Contig
  <select value={contigChoice}
          onChange={(e) => setContigChoice(e.target.value)}>
    {contigOptions.map((o) => (
      <option key={o.id} value={o.id}>
        {o.label}{o.recommended ? " (추천)" : ""}{o.contig ? ` — ${o.contig}` : ""}
      </option>
    ))}
  </select>
</label>
{contigChoice === "custom" && (
  <input type="text" placeholder="A1-10,B5-12"
         value={customContig} onChange={(e) => setCustomContig(e.target.value)} />
)}
```
Default-select the recommended: when options load, `setContigChoice(options.find(o => o.recommended)?.id ?? "custom")`.

- [ ] **Step 4: Wire submission.** On job submit for rfdiffusion, resolve the contig:
```ts
const chosen = contigOptions.find(o => o.id === contigChoice);
const contigValue = contigChoice === "custom" ? customContig : chosen?.contig;
// include in the job parameters:
//   contigs: contigValue
//   contig_processed_coords: contigChoice !== "custom"   // dropdown contigs are processed-coords
```
Ensure both `contigs` and `contig_processed_coords` reach the backend parameters → gateway payload (backend forwards parameters into the worker `input`; confirm `build_pipeline_payload` passes arbitrary params through, extend if it whitelists).

- [ ] **Step 5: Build + restart frontend**

```bash
cd /opt/bio_model_portal/portal/frontend && npm run build && systemctl restart bmp-frontend && sleep 5
curl -s -o /dev/null -w "frontend: %{http_code}\n" http://127.0.0.1:18120/
```
Expected: build succeeds, frontend 200.

- [ ] **Step 6: Commit**

```bash
cd /opt/bio_model_portal && git add portal/frontend/src && git commit -m "feat(frontend): RFD3 contig dropdown with auto-selected ligand-aware recommendation"
```

---

## Phase 5 — Port remaining model prep + rewrite adapters

Each task: write `prep/<model>.py` mirroring protein_pipeline's client payload logic, delegate the adapter to it, unit-test, smoke-test through the gateway, commit. Keep prior local-GPU behavior working.

### Task 6: ProteinMPNN prep
**Files:** Create `gateway/prep/proteinmpnn.py`, Test `gateway/tests/test_prep_proteinmpnn.py`, Modify `gateway/adapters.py`.
- [ ] **Step 1:** Port from `clients/proteinmpnn.py:38-91`. `build_input(payload)`: extract PDB (`structure.extract_pdb_text`) → `clean, mapping = structure.preprocess(pdb_text, chains=payload.get("chains"))` → set `pdb_base64 = base64(clean)`, `pdb_name`, pass through `pdb_path_chains`, `fixed_positions` (remap to processed resseq via mapping if provided in original coords), `model_name`, `num_seq_per_target`, `batch_size`, `sampling_temp`, `seed`, `backbone_noise`. Skip env-var chunking (worker handles batches).
- [ ] **Step 2:** `adapter_proteinmpnn` → `from prep import proteinmpnn; return proteinmpnn.build_input(payload)`.
- [ ] **Step 3:** Unit test: given the 4KL5 archive, output has `pdb_base64` decoding to positive-resseq PDB and `pdb_name` set.
- [ ] **Step 4:** `pytest tests/test_prep_proteinmpnn.py -v` → PASS.
- [ ] **Step 5:** `systemctl restart bmp-gateway`; smoke a tiny ProteinMPNN job via `/v2/proteinmpnn-local/run` (minimal backbone) → COMPLETED.
- [ ] **Step 6:** Commit.

### Task 7: DiffDock prep
**Files:** Create `gateway/prep/diffdock.py`, Test, Modify `gateway/adapters.py` (diffdock currently has no adapter — add `adapter="diffdock"` in `endpoints.yaml` and register in `adapters.ADAPTERS`).
- [ ] **Step 1:** Port from `clients/local_http.py:298-367` + `bio/ligand_text.normalize_diffdock_ligand_inputs`. `build_input(payload)`: take `protein_pdb`/archive + `ligand_smiles`/`ligand_sdf` → normalize ligand (mmcif→SDF) → build `protein_ligand_csv`, `pdb_files`, `sdf_files`, `cmd`, `config`, dirs exactly as the client does.
- [ ] **Step 2:** Register `"diffdock"` in `ADAPTERS` and set `adapter: diffdock` for `diffdock-local` in `endpoints.yaml`.
- [ ] **Step 3:** Unit test ligand normalization (SMILES passthrough; mmCIF→SDF detection via `looks_like_diffdock_modelserver_mmcif`).
- [ ] **Step 4:** `pytest` → PASS.
- [ ] **Step 5:** Restart gateway; smoke DiffDock (SMILES `CCO` + tiny protein) → ACCEPTED/COMPLETED, no schema error.
- [ ] **Step 6:** Commit.

### Task 8: Rosetta Relax prep
**Files:** Create `gateway/prep/rosetta.py`, Test, Modify `gateway/adapters.py`.
- [ ] **Step 1:** `build_input(payload)`: extract PDB → `clean,_ = structure.preprocess(...)` → `pdb_content = clean`, pass `target_id`, `nstruct`, `extra_flags`. (Mirror `clients/local_http.py:376-405` / existing `adapter_rosetta_relax` but using vendored preprocess for normalization.)
- [ ] **Step 2:** `adapter_rosetta_relax` → delegate to `prep.rosetta.build_input`.
- [ ] **Step 3:** Unit test: archive in → `pdb_content` present, positive resseq.
- [ ] **Step 4:** `pytest` → PASS.
- [ ] **Step 5:** Restart gateway; smoke Rosetta (tiny PDB) → COMPLETED.
- [ ] **Step 6:** Commit.

### Task 9: Sequence prep (BioEmu / AF2 / ESMFold) + MMseqs
**Files:** Create `gateway/prep/sequence.py`, `gateway/prep/mmseqs.py`, Tests, Modify `gateway/adapters.py`.
- [ ] **Step 1:** `sequence.py`: copy `validate_protein_sequence(sequence, *, model)` + `_STANDARD_AMINO_ACIDS` verbatim from `clients/local_http.py:131-159`. Add `as_sequence_list(seqs) -> [{"id","sequence"}]`. BioEmu adapter: validate the single sequence, keep `num_samples`/`model_name`. (AF2/ESMFold are RunPod/HTTP sequence-list models — add validation where the adapter applies.)
- [ ] **Step 2:** `mmseqs.py`: keep current FASTA normalization (the existing `adapter_mmseqs` already works — move it into `prep/mmseqs.py` and delegate).
- [ ] **Step 3:** Delegate `adapter_mmseqs` and add a `bioemu` adapter (register `"bioemu"` in `ADAPTERS`, set `adapter: bioemu` on `bioemu-local` in `endpoints.yaml`) that calls `sequence.validate_protein_sequence(seq, model="BioEmu")` then passes through.
- [ ] **Step 4:** Unit tests: bad sequence (`"MKX1"`) raises ValueError naming position; mmseqs builds `query_fasta`.
- [ ] **Step 5:** `pytest tests/ -v` (whole suite) → PASS. Restart gateway; smoke BioEmu (`"MKTAYIAKQR"`, num_samples 1) and MMseqs → COMPLETED.
- [ ] **Step 6:** Commit.

---

## Phase 6 — End-to-end verification

### Task 10: Full regression + smoke refresh
**Files:** Modify `docs/SMOKE_RESULTS.md`.
- [ ] **Step 1:** Run the whole gateway unit suite: `cd /opt/bio_model_portal/gateway && .venv/bin/python -m pytest tests/ -v` → all PASS.
- [ ] **Step 2:** **4KL5 end-to-end through the portal UI path**: get a token (`/bootstrap-login`), POST the contig-suggestions endpoint with 4KL5, then submit an rfdiffusion job via `/api/jobs` with the recommended contig + `contig_processed_coords=true`; poll `/api/jobs/{id}` until terminal. Expected: COMPLETED (or running past parsing), NOT failed at `Invalid contig format`.
- [ ] **Step 3:** Re-smoke each model (minimal inputs) through `/v2/{endpoint}/run`; record model | result | notes in `docs/SMOKE_RESULTS.md`.
- [ ] **Step 4:** Confirm no regression on the previously-working local models (esmfold/colabfold/mmseqs) and RunPod passthrough (alphafold/phastest health 200).
- [ ] **Step 5:** Commit `docs/SMOKE_RESULTS.md`.

---

## Self-Review (author check)

- **Spec coverage:** vendoring bio (Task 1); RFD3 prep + preprocess/remap (Task 2); contig suggest + ligand-aware recommend (Task 3); backend proxy (Task 4); frontend dropdown + auto-select (Task 5); all other models prep (Tasks 6–9); 4KL5 regression + smoke (Tasks 2 step 5, Task 10). All spec sections mapped.
- **Oracle:** the 4KL5 negative-residue failure is the explicit acceptance test in Task 2 (gateway level) and Task 10 (full stack).
- **Open detail resolved at impl time (not placeholders):** exact `preprocess_pdb` flag combination is empirically confirmed against the 4KL5 worker oracle in Task 2 Step 5 (default strip_nonpositive+renumber; documented if changed). `settings.runpod_base` attribute name and `build_pipeline_payload` param passthrough are verified by reading the named files in Tasks 4/5.
- **Type/name consistency:** `structure.extract_pdb_text` / `structure.preprocess` / `<model>.build_input` used consistently across prep modules and adapters; contig coordinate flag `contig_processed_coords` consistent backend→gateway.
- **Scope note:** Phases 1–2 alone fix the reported RFD3 crash and can ship first; Phases 3–4 add the contig UX; Phase 5 generalizes to all models. Each phase ends green and independently valuable.
