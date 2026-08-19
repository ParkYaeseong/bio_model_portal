from __future__ import annotations

import base64
import csv
import io
import re

from bio import ligand_text
from . import structure


_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SAFE_PATH = re.compile(r"^[A-Za-z0-9._/-]{1,256}$")


def _safe_name(value: str, field: str) -> str:
    v = str(value or "").strip()
    if not _SAFE_NAME.match(v):
        raise ValueError(f"invalid {field}: must match [A-Za-z0-9_-]{{1,64}}")
    return v


def _safe_path(value: str, field: str, default: str) -> str:
    v = str(value or default).strip()
    if ".." in v or not _SAFE_PATH.match(v):
        raise ValueError(f"invalid {field}")
    return v


def _encode_text_file(name: str, content: str) -> dict[str, str]:
    """Mirror protein_pipeline LocalHTTPDiffDockClient._encode_text_file."""
    data = str(content or "").encode("utf-8", errors="replace")
    return {"filename": name, "data_b64": base64.b64encode(data).decode("ascii")}


def build_input(payload: dict) -> dict:
    """Build the DiffDock worker input from the portal payload.

    If the payload is already a fully-formed worker payload (has
    ``protein_ligand_csv``), pass it through unchanged (minus internal keys).
    The passthrough branch trusts the portal-backend-constructed payload
    (_build_diffdock_payload in jobs.py) which already validates inputs and
    builds the CSV with csv.writer.  complex_name in a passthrough payload is
    NOT re-validated here — it is already embedded in the pre-built CSV/cmd.

    Otherwise build the worker shape from protein PDB + ligand SMILES/SDF,
    exactly as protein_pipeline's LocalHTTPDiffDockClient.dock() does.
    """
    out = dict(payload)

    # Already worker-ready (e.g. portal _build_diffdock_payload): pass through.
    if "protein_ligand_csv" in out:
        out.pop("input_archive", None)
        return out

    # Validate complex_name — it is used in filenames, CSV fields, and cmd.
    complex_name = _safe_name(out.get("complex_name") or "complex", "complex_name")

    pdb_text = structure.extract_pdb_text(out)
    if not pdb_text:
        pdb_text = str(out.get("protein_pdb") or "")
    if not str(pdb_text or "").strip():
        raise ValueError("DiffDock requires protein_pdb text")

    ligand_sdf = out.get("ligand_sdf")
    if not str(ligand_sdf or "").strip() and not str(out.get("ligand_smiles") or "").strip():
        # MCP/chat path: the ligand may have been attached as an .sdf inside the
        # upload archive rather than typed as a SMILES/ligand_sdf parameter.
        ligand_sdf = structure.extract_ligand_text(out)
    smiles, sdf = ligand_text.normalize_diffdock_ligand_inputs(
        out.get("ligand_smiles"), ligand_sdf
    )
    if not (smiles or sdf):
        raise ValueError("DiffDock requires ligand_smiles or ligand_sdf")

    # config and out_dir: validate paths; defaults match the portal constants.
    # Neither is exposed by the portal UI (_build_diffdock_payload always uses
    # the hardcoded DIFFDOCK_CONFIG / DIFFDOCK_OUT_DIR), but validate defensively.
    config = _safe_path(out.get("config"), "config", "default_inference_args.yaml")
    out_dir = _safe_path(out.get("out_dir"), "out_dir", "results/")

    # extra_args is NOT exposed by the portal (_build_diffdock_payload hard-codes
    # extra_args=""). Drop any caller-supplied value to prevent shell injection.
    # If this ever needs to be re-enabled, require it to be a list of pre-validated
    # tokens and shlex.quote each one before joining.

    protein_name = f"{complex_name}.pdb"
    ligand_name = f"{complex_name}.sdf"
    csv_name = "input_protein_ligand_info.csv"
    ligand_desc = str(smiles) if smiles else f"inputs/{ligand_name}"

    # Build CSV with csv.writer so any special characters in field values
    # (commas, quotes, newlines in SMILES or ligand_description) are properly
    # escaped rather than injected raw into the CSV.
    csv_buf = io.StringIO()
    writer = csv.writer(csv_buf)
    writer.writerow(["complex_name", "protein_path", "ligand_description", "protein_sequence"])
    writer.writerow([complex_name, f"inputs/{protein_name}", ligand_desc, ""])
    csv_text = csv_buf.getvalue()

    cmd = (
        f"python3 -m inference --config {config}"
        f" --protein_ligand_csv data/{csv_name}"
        f" --out_dir {out_dir}"
    )
    # extra_args deliberately omitted — not portal-exposed, dropped to prevent injection.

    return {
        "cmd": cmd,
        "protein_ligand_csv": _encode_text_file(csv_name, csv_text),
        "pdb_files": [_encode_text_file(protein_name, pdb_text)],
        "sdf_files": [_encode_text_file(ligand_name, str(sdf))] if sdf else [],
        "data_dir": "data",
        "inputs_dir": "inputs",
        "out_dir": out_dir,
        "config": config,
        "extra_args": "",
    }
