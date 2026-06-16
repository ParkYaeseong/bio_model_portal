# VENDORED from protein_pipeline pipeline-mcp/src/pipeline_mcp/bio/ on 2026-06-16.
# Pure stdlib, no config deps. Re-sync manually if the upstream changes.
from __future__ import annotations

from dataclasses import dataclass
import re


_TOKEN_RE = re.compile(r'"[^"]*"|\'[^\']*\'|\S+')
_MOLFILE_CHARGE_CODES = {
    3: 1,
    2: 2,
    1: 3,
    -1: 5,
    -2: 6,
    -3: 7,
}
_BOND_TYPES = {
    "sing": 1,
    "single": 1,
    "doub": 2,
    "double": 2,
    "trip": 3,
    "triple": 3,
    "quad": 4,
    "arom": 4,
    "delo": 4,
}


@dataclass(frozen=True)
class _MmcifAtom:
    atom_id: str
    element: str
    x: float
    y: float
    z: float
    comp_id: str
    auth_seq_id: str
    auth_asym_id: str
    formal_charge: int | None


@dataclass(frozen=True)
class _MmcifBond:
    atom_id_1: str
    atom_id_2: str
    comp_id: str
    order: str
    aromatic: bool


def looks_like_diffdock_modelserver_mmcif(text: str | None) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    return raw.startswith("data_") and "_atom_site." in raw and "_chem_comp_bond." in raw


def looks_like_sdf_text(text: str | None) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    return "M  END" in raw or "$$$$" in raw


def normalize_diffdock_ligand_inputs(
    ligand_smiles: str | None = None,
    ligand_sdf: str | None = None,
) -> tuple[str | None, str | None]:
    smiles = str(ligand_smiles or "").strip() or None
    sdf = str(ligand_sdf or "").strip() or None

    if sdf and looks_like_diffdock_modelserver_mmcif(sdf):
        return None, mmcif_ligand_to_sdf(sdf)
    if smiles and looks_like_diffdock_modelserver_mmcif(smiles):
        return None, mmcif_ligand_to_sdf(smiles)
    return smiles, sdf


def _clean_token(token: str) -> str:
    value = str(token or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _parse_float(token: str) -> float:
    return float(str(token or "").strip())


def _parse_int(token: str) -> int | None:
    value = str(token or "").strip()
    if not value or value in {"?", "."}:
        return None
    return int(value)


def _tokenize_cif_row(line: str) -> list[str]:
    return [_clean_token(token) for token in _TOKEN_RE.findall(line)]


def _iter_cif_loops(text: str):
    lines = [line.rstrip("\n") for line in str(text or "").splitlines()]
    i = 0
    while i < len(lines):
        current = lines[i].strip()
        if current != "loop_":
            i += 1
            continue
        i += 1
        headers: list[str] = []
        while i < len(lines):
            current = lines[i].strip()
            if current.startswith("_"):
                headers.append(current)
                i += 1
                continue
            break
        rows: list[list[str]] = []
        while i < len(lines):
            current = lines[i].strip()
            if not current:
                i += 1
                continue
            if current == "#":
                i += 1
                break
            if current == "loop_" or current.startswith("_"):
                break
            rows.append(_tokenize_cif_row(lines[i]))
            i += 1
        if headers:
            yield headers, rows


def _find_cif_loop(text: str, prefix: str) -> tuple[list[str], list[list[str]]]:
    for headers, rows in _iter_cif_loops(text):
        if headers and headers[0].startswith(prefix):
            return headers, rows
    raise ValueError(f"mmCIF parse failed: missing {prefix} loop")


def _index_headers(headers: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for idx, header in enumerate(headers):
        out[header.rsplit(".", 1)[-1]] = idx
    return out


def _row_value(row: list[str], idx: dict[str, int], key: str, default: str = "") -> str:
    column = idx.get(key)
    if column is None or column >= len(row):
        return default
    return row[column]


def _parse_mmcif_atoms(text: str) -> list[_MmcifAtom]:
    headers, rows = _find_cif_loop(text, "_atom_site.")
    idx = _index_headers(headers)
    atoms: list[_MmcifAtom] = []
    for row in rows:
        if len(row) < len(headers):
            continue
        atom_id = _clean_token(row[idx["label_atom_id"]])
        element = _clean_token(row[idx["type_symbol"]]).upper() or atom_id[:1].upper()
        atoms.append(
            _MmcifAtom(
                atom_id=atom_id,
                element=element,
                x=_parse_float(row[idx["Cartn_x"]]),
                y=_parse_float(row[idx["Cartn_y"]]),
                z=_parse_float(row[idx["Cartn_z"]]),
                comp_id=_clean_token(row[idx["label_comp_id"]]),
                auth_seq_id=_clean_token(
                    _row_value(row, idx, "auth_seq_id", _row_value(row, idx, "label_seq_id", ""))
                ),
                auth_asym_id=_clean_token(
                    _row_value(row, idx, "auth_asym_id", _row_value(row, idx, "label_asym_id", ""))
                ),
                formal_charge=_parse_int(_row_value(row, idx, "pdbx_formal_charge", "")),
            )
        )
    if not atoms:
        raise ValueError("mmCIF parse failed: no ligand atoms found")
    return atoms


def _parse_mmcif_bonds(text: str) -> list[_MmcifBond]:
    headers, rows = _find_cif_loop(text, "_chem_comp_bond.")
    idx = _index_headers(headers)
    bonds: list[_MmcifBond] = []
    for row in rows:
        if len(row) < len(headers):
            continue
        aromatic_token = _clean_token(_row_value(row, idx, "pdbx_aromatic_flag", "")).lower()
        bonds.append(
            _MmcifBond(
                atom_id_1=_clean_token(row[idx["atom_id_1"]]),
                atom_id_2=_clean_token(row[idx["atom_id_2"]]),
                comp_id=_clean_token(row[idx["comp_id"]]),
                order=_clean_token(row[idx["value_order"]]).lower(),
                aromatic=aromatic_token in {"y", "yes", "true"},
            )
        )
    if not bonds:
        raise ValueError("mmCIF parse failed: no ligand bonds found")
    return bonds


def _bond_type(order: str, aromatic: bool) -> int:
    if aromatic:
        return 4
    return _BOND_TYPES.get(str(order or "").strip().lower(), 1)


def _molfile_charge_code(charge: int | None) -> int:
    if charge is None:
        return 0
    return _MOLFILE_CHARGE_CODES.get(charge, 0)


def mmcif_ligand_to_sdf(text: str) -> str:
    atoms = _parse_mmcif_atoms(text)
    bonds = _parse_mmcif_bonds(text)

    first = atoms[0]
    residue_key = (first.comp_id, first.auth_seq_id, first.auth_asym_id)
    filtered_atoms = [atom for atom in atoms if (atom.comp_id, atom.auth_seq_id, atom.auth_asym_id) == residue_key]
    if not filtered_atoms:
        filtered_atoms = atoms

    atom_index = {atom.atom_id: idx + 1 for idx, atom in enumerate(filtered_atoms)}
    filtered_bonds = [
        bond
        for bond in bonds
        if bond.comp_id == first.comp_id and bond.atom_id_1 in atom_index and bond.atom_id_2 in atom_index
    ]
    if not filtered_bonds:
        raise ValueError("mmCIF parse failed: no usable bonds for ligand instance")

    lines = [
        first.comp_id or "LIG",
        "protein_pipeline",
        "mmCIF to SDF",
        f"{len(filtered_atoms):>3}{len(filtered_bonds):>3}  0  0  0  0            999 V2000",
    ]
    for atom in filtered_atoms:
        charge_code = _molfile_charge_code(atom.formal_charge)
        lines.append(
            f"{atom.x:10.4f}{atom.y:10.4f}{atom.z:10.4f} {atom.element:<3s} 0{charge_code:>3}  0  0  0  0  0  0  0  0  0  0"
        )
    for bond in filtered_bonds:
        lines.append(
            f"{atom_index[bond.atom_id_1]:>3}{atom_index[bond.atom_id_2]:>3}{_bond_type(bond.order, bond.aromatic):>3}  0  0  0  0"
        )
    lines.extend(["M  END", "$$$$"])
    return "\n".join(lines) + "\n"
