from __future__ import annotations

import base64

from bio import ligand_text
from . import structure


def _encode_text_file(name: str, content: str) -> dict[str, str]:
    """Mirror protein_pipeline LocalHTTPDiffDockClient._encode_text_file."""
    data = str(content or "").encode("utf-8", errors="replace")
    return {"filename": name, "data_b64": base64.b64encode(data).decode("ascii")}


def build_input(payload: dict) -> dict:
    """Build the DiffDock worker input from the portal payload.

    If the payload is already a fully-formed worker payload (has
    ``protein_ligand_csv``), pass it through unchanged (minus internal keys).
    Otherwise build the worker shape from protein PDB + ligand SMILES/SDF,
    exactly as protein_pipeline's LocalHTTPDiffDockClient.dock() does.
    """
    out = dict(payload)

    # Already worker-ready (e.g. portal _build_diffdock_payload): pass through.
    if "protein_ligand_csv" in out:
        out.pop("input_archive", None)
        return out

    complex_name = str(out.get("complex_name") or "complex")

    pdb_text = structure.extract_pdb_text(out)
    if not pdb_text:
        pdb_text = str(out.get("protein_pdb") or "")
    if not str(pdb_text or "").strip():
        raise ValueError("DiffDock requires protein_pdb text")

    smiles, sdf = ligand_text.normalize_diffdock_ligand_inputs(
        out.get("ligand_smiles"), out.get("ligand_sdf")
    )
    if not (smiles or sdf):
        raise ValueError("DiffDock requires ligand_smiles or ligand_sdf")

    config = str(out.get("config") or "default_inference_args.yaml")
    out_dir = str(out.get("out_dir") or "results/")
    extra_args = str(out.get("extra_args") or "")
    protein_name = f"{complex_name}.pdb"
    ligand_name = f"{complex_name}.sdf"
    csv_name = "input_protein_ligand_info.csv"
    ligand_desc = str(smiles) if smiles else f"inputs/{ligand_name}"
    csv_text = "\n".join(
        [
            "complex_name,protein_path,ligand_description,protein_sequence",
            f"{complex_name},inputs/{protein_name},{ligand_desc},",
        ]
    ) + "\n"
    cmd = f"python3 -m inference --config {config} --protein_ligand_csv data/{csv_name} --out_dir {out_dir}"
    if extra_args:
        cmd = f"{cmd} {extra_args}".strip()

    return {
        "cmd": cmd,
        "protein_ligand_csv": _encode_text_file(csv_name, csv_text),
        "pdb_files": [_encode_text_file(protein_name, pdb_text)],
        "sdf_files": [_encode_text_file(ligand_name, str(sdf))] if sdf else [],
        "data_dir": "data",
        "inputs_dir": "inputs",
        "out_dir": out_dir,
        "config": config,
        "extra_args": extra_args,
    }
