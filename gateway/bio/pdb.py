# VENDORED from protein_pipeline pipeline-mcp/src/pipeline_mcp/bio/ on 2026-06-16.
# Pure stdlib, no config deps. Re-sync manually if the upstream changes.
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import math
import re


_WATER_RESNAMES = {"HOH", "WAT", "H2O"}
_AA3_TO_AA1: dict[str, str] = {
    "ALA": "A",
    "ARG": "R",
    "ASN": "N",
    "ASP": "D",
    "CYS": "C",
    "GLN": "Q",
    "GLU": "E",
    "GLY": "G",
    "HIS": "H",
    "ILE": "I",
    "LEU": "L",
    "LYS": "K",
    "MET": "M",
    "PHE": "F",
    "PRO": "P",
    "SER": "S",
    "THR": "T",
    "TRP": "W",
    "TYR": "Y",
    "VAL": "V",
    # Common variants / modified residues
    "MSE": "M",  # selenomethionine
    "SEC": "U",  # selenocysteine
    "PYL": "O",  # pyrrolysine
    "HSD": "H",
    "HSE": "H",
    "HSP": "H",
    "CYX": "C",
    "ASX": "B",
    "GLX": "Z",
    "UNK": "X",
}


@dataclass(frozen=True)
class Atom:
    record: str  # ATOM or HETATM
    atom_name: str
    resname: str
    chain_id: str
    resseq: int
    icode: str
    x: float
    y: float
    z: float
    element: str


@dataclass(frozen=True)
class Residue:
    chain_id: str
    index: int  # 1-based within chain
    resname: str
    resseq: int
    icode: str
    atoms: tuple[Atom, ...]


def _parse_int(value: str, default: int = 0) -> int:
    try:
        return int(value.strip())
    except Exception:
        return default


def _parse_float(value: str, default: float = 0.0) -> float:
    try:
        return float(value.strip())
    except Exception:
        return default


_CIF_TOKEN_RE = re.compile(
    r"""
    '(?:[^']|'')*'
    |"(?:[^"]|"")*"
    |(?:;[^\n]*(?:\n(?!;).*)*\n;)
    |[^\s]+
    """,
    re.VERBOSE,
)


def looks_like_mmcif(text: str) -> bool:
    raw = str(text or "").lstrip()
    return raw.lower().startswith("data_") and "_atom_site." in raw


def _clean_cif_token(value: str) -> str:
    text = str(value or "")
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        return text[1:-1].replace("''", "'")
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return text[1:-1].replace('""', '"')
    if text.startswith(";") and text.endswith("\n;"):
        return text[1:-2].strip("\n")
    return text


def _cif_tokens(text: str) -> list[str]:
    return [_clean_cif_token(match.group(0)) for match in _CIF_TOKEN_RE.finditer(str(text or ""))]


def _atom_site_loop(tokens: list[str]) -> tuple[list[str], list[list[str]]]:
    idx = 0
    while idx < len(tokens):
        if tokens[idx].lower() != "loop_":
            idx += 1
            continue
        idx += 1
        headers: list[str] = []
        while idx < len(tokens) and tokens[idx].startswith("_"):
            headers.append(tokens[idx])
            idx += 1
        if not headers:
            continue
        lower_headers = [header.lower() for header in headers]
        if not any(header.startswith("_atom_site.") for header in lower_headers):
            # Skip row values for unrelated loops.
            while idx < len(tokens) and tokens[idx].lower() != "loop_" and not tokens[idx].startswith("_"):
                idx += 1
            continue
        width = len(headers)
        rows: list[list[str]] = []
        while idx < len(tokens) and tokens[idx].lower() != "loop_" and not tokens[idx].startswith("_"):
            row = tokens[idx : idx + width]
            if len(row) < width:
                break
            rows.append(row)
            idx += width
        return headers, rows
    return [], []


def _header_index(headers: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for index, header in enumerate(headers):
        normalized = header.lower()
        if normalized.startswith("_atom_site."):
            normalized = normalized[len("_atom_site.") :]
        out[normalized] = index
    return out


def _row_get(row: list[str], header_map: dict[str, int], *names: str, default: str = "") -> str:
    for name in names:
        index = header_map.get(name.lower())
        if index is None or index >= len(row):
            continue
        value = str(row[index] or "").strip()
        if value and value not in {".", "?"}:
            return value
    return default


def _format_pdb_atom_name(atom_name: str, element: str) -> str:
    atom = str(atom_name or "").strip().replace('"', "").replace("'", "")[:4]
    elem = str(element or "").strip().upper()[:2]
    if len(atom) < 4 and len(elem) == 1 and not atom.startswith(elem):
        return f" {atom:<3}"[:4]
    return f"{atom:<4}"[:4]


def _pdb_resseq(value: str, fallback: int) -> int:
    parsed = _parse_int(str(value or ""), default=0)
    return parsed if parsed else fallback


def mmcif_to_pdb(text: str) -> str:
    tokens = _cif_tokens(text)
    headers, rows = _atom_site_loop(tokens)
    if not headers or not rows:
        raise ValueError("mmCIF parse failed: _atom_site loop not found")
    header_map = _header_index(headers)
    out: list[str] = []
    fallback_resseq_by_key: dict[tuple[str, str], int] = {}

    for row_index, row in enumerate(rows, start=1):
        record = _row_get(row, header_map, "group_pdb", default="ATOM").upper()
        record = "HETATM" if record == "HETATM" else "ATOM"
        serial = _parse_int(_row_get(row, header_map, "id"), row_index)
        atom_name_raw = _row_get(row, header_map, "auth_atom_id", "label_atom_id", default="X")
        element = _row_get(row, header_map, "type_symbol", default=atom_name_raw[:1]).upper()[:2]
        atom_name = _format_pdb_atom_name(atom_name_raw, element)
        altloc = _row_get(row, header_map, "label_alt_id", "pdbx_pdb_alt_id", default="")
        altloc = "" if altloc in {".", "?"} else altloc[:1]
        resname = _row_get(row, header_map, "auth_comp_id", "label_comp_id", default="UNK").upper()[:3]
        chain = _row_get(row, header_map, "auth_asym_id", "label_asym_id", default="_")[:1] or "_"
        seq_raw = _row_get(row, header_map, "auth_seq_id", "label_seq_id")
        residue_key = (chain, seq_raw or str(row_index))
        if residue_key not in fallback_resseq_by_key:
            fallback_resseq_by_key[residue_key] = len(
                [key for key in fallback_resseq_by_key if key[0] == chain]
            ) + 1
        resseq = _pdb_resseq(seq_raw, fallback_resseq_by_key[residue_key])
        icode = _row_get(row, header_map, "pdbx_pdb_ins_code", "ins_code", default="")
        icode = "" if icode in {".", "?"} else icode[:1]
        x = _parse_float(_row_get(row, header_map, "cartn_x"))
        y = _parse_float(_row_get(row, header_map, "cartn_y"))
        z = _parse_float(_row_get(row, header_map, "cartn_z"))
        occupancy = _parse_float(_row_get(row, header_map, "occupancy"), 1.0)
        bfactor = _parse_float(_row_get(row, header_map, "b_iso_or_equiv"), 0.0)
        out.append(
            f"{record:<6}{serial % 100000:5d} {atom_name}{altloc:1}{resname:>3} {chain:1}"
            f"{resseq:4d}{icode:1}   {x:8.3f}{y:8.3f}{z:8.3f}{occupancy:6.2f}{bfactor:6.2f}"
            f"          {element:>2}\n"
        )

    if not out:
        raise ValueError("mmCIF parse failed: no atom rows found")
    out.append("END\n")
    return "".join(out)


def strip_to_first_model(pdb_text: str) -> str:
    """Keep only the first MODEL of a multi-model PDB (e.g. an NMR ensemble).

    Multi-model input would otherwise be read as one concatenated chain -- e.g. a
    75-residue domain across 20 NMR models becomes a 1500-residue sequence --
    corrupting downstream sequence extraction. Single-model or model-less input is
    returned unchanged.
    """
    lines = pdb_text.splitlines(keepends=True)
    if not any(line[:5] == "MODEL" for line in lines):
        return pdb_text
    out: list[str] = []
    model_count = 0
    for line in lines:
        rec = line[:6].strip().upper()
        if rec == "MODEL":
            model_count += 1
            if model_count >= 2:
                break
            out.append(line)
            continue
        if rec == "ENDMDL":
            out.append(line)
            break
        out.append(line)
    return "".join(out)


def normalize_structure_text(text: str) -> str:
    raw = str(text or "")
    pdb = mmcif_to_pdb(raw) if looks_like_mmcif(raw) else raw
    return strip_to_first_model(pdb)


def iter_atoms(pdb_text: str):
    for raw in pdb_text.splitlines():
        if not raw:
            continue
        rec = raw[:6].strip().upper()
        if rec not in {"ATOM", "HETATM"}:
            continue
        atom_name = raw[12:16].strip()
        resname = raw[17:20].strip()
        chain_id = raw[21:22].strip() or "_"
        resseq = _parse_int(raw[22:26])
        icode = raw[26:27].strip()
        x = _parse_float(raw[30:38])
        y = _parse_float(raw[38:46])
        z = _parse_float(raw[46:54])
        element = raw[76:78].strip() or (atom_name[:1].strip().upper() if atom_name else "")
        yield Atom(
            record=rec,
            atom_name=atom_name,
            resname=resname,
            chain_id=chain_id,
            resseq=resseq,
            icode=icode,
            x=x,
            y=y,
            z=z,
            element=element.upper(),
        )


def residues_by_chain(pdb_text: str, *, only_atom_records: bool = True) -> dict[str, list[Residue]]:
    residues: dict[str, list[Residue]] = {}
    current_key: tuple[str, int, str] | None = None
    current_atoms: list[Atom] = []
    current_resname = ""
    current_chain = ""
    current_resseq = 0
    current_icode = ""
    chain_indices: dict[str, int] = {}

    def flush() -> None:
        nonlocal current_key, current_atoms, current_resname, current_chain, current_resseq, current_icode
        if current_key is None:
            return
        chain = current_chain
        chain_indices[chain] = chain_indices.get(chain, 0) + 1
        idx = chain_indices[chain]
        residues.setdefault(chain, []).append(
            Residue(
                chain_id=chain,
                index=idx,
                resname=current_resname,
                resseq=current_resseq,
                icode=current_icode,
                atoms=tuple(current_atoms),
            )
        )
        current_key = None
        current_atoms = []
        current_resname = ""
        current_chain = ""
        current_resseq = 0
        current_icode = ""

    for atom in iter_atoms(pdb_text):
        if only_atom_records and atom.record != "ATOM":
            continue
        key = (atom.chain_id, atom.resseq, atom.icode)
        if current_key is None:
            current_key = key
            current_chain = atom.chain_id
            current_resseq = atom.resseq
            current_icode = atom.icode
            current_resname = atom.resname
            current_atoms = [atom]
            continue
        if key != current_key:
            flush()
            current_key = key
            current_chain = atom.chain_id
            current_resseq = atom.resseq
            current_icode = atom.icode
            current_resname = atom.resname
            current_atoms = [atom]
            continue
        current_atoms.append(atom)
    flush()
    return residues


def sequence_by_chain(
    pdb_text: str,
    *,
    chains: list[str] | None = None,
    unknown: str = "X",
) -> dict[str, str]:
    residues = residues_by_chain(pdb_text, only_atom_records=True)
    chain_set = set(chains) if chains is not None else None

    out: dict[str, str] = {}
    for chain_id, res_list in residues.items():
        if chain_set is not None and chain_id not in chain_set:
            continue
        seq = "".join(_AA3_TO_AA1.get(res.resname.upper(), unknown) for res in res_list)
        if seq:
            out[chain_id] = seq
    return out


def _is_heavy(atom: Atom) -> bool:
    return atom.element not in {"H", "D"}


def ligand_atoms_present(
    pdb_text: str,
    *,
    chains: list[str] | None = None,
    ligand_resnames: list[str] | None = None,
    ligand_atom_chains: list[str] | None = None,
) -> bool:
    ligand_set = {name.strip().upper() for name in ligand_resnames or [] if name.strip()} or None
    atom_chain_set = {c.strip() for c in (ligand_atom_chains or []) if str(c).strip()} or None
    masked_chain_set = set(chains) if chains is not None else set()

    for atom in iter_atoms(pdb_text):
        if atom.record == "HETATM":
            if atom.resname.upper() in _WATER_RESNAMES:
                continue
            if ligand_set is not None and atom.resname.upper() not in ligand_set:
                continue
            if _is_heavy(atom):
                return True
            continue

        if atom.record == "ATOM" and atom_chain_set is not None:
            if atom.chain_id not in atom_chain_set:
                continue
            if atom.chain_id in masked_chain_set:
                continue
            if _is_heavy(atom):
                return True
    return False


def ligand_proximity_mask(
    pdb_text: str,
    *,
    chains: list[str] | None = None,
    distance_angstrom: float = 6.0,
    ligand_resnames: list[str] | None = None,
    ligand_atom_chains: list[str] | None = None,
) -> dict[str, list[int]]:
    ligand_set = {name.strip().upper() for name in ligand_resnames or [] if name.strip()} or None
    atom_chain_set = {c.strip() for c in (ligand_atom_chains or []) if str(c).strip()} or None
    masked_chain_set = set(chains) if chains is not None else set()

    ligand_atoms: list[Atom] = []
    for atom in iter_atoms(pdb_text):
        if atom.record == "HETATM":
            if atom.resname.upper() in _WATER_RESNAMES:
                continue
            if ligand_set is not None and atom.resname.upper() not in ligand_set:
                continue
            if _is_heavy(atom):
                ligand_atoms.append(atom)
            continue

        if atom.record == "ATOM" and atom_chain_set is not None:
            if atom.chain_id not in atom_chain_set:
                continue
            if atom.chain_id in masked_chain_set:
                continue
            if _is_heavy(atom):
                ligand_atoms.append(atom)

    if not ligand_atoms:
        return {chain: [] for chain in (chains or [])} if chains else {}

    dist2 = float(distance_angstrom) ** 2
    residues = residues_by_chain(pdb_text, only_atom_records=True)

    mask: dict[str, list[int]] = {}
    for chain_id, res_list in residues.items():
        if chains is not None and chain_id not in set(chains):
            continue
        hits: list[int] = []
        for res in res_list:
            close = False
            for at in res.atoms:
                if not _is_heavy(at):
                    continue
                for lig in ligand_atoms:
                    dx = at.x - lig.x
                    dy = at.y - lig.y
                    dz = at.z - lig.z
                    if (dx * dx + dy * dy + dz * dz) <= dist2:
                        close = True
                        break
                if close:
                    break
            if close:
                hits.append(res.index)
        if hits:
            mask[chain_id] = hits
    return mask


def _rewrite_pdb_resseq_icode(raw: str, *, resseq: int, icode: str) -> str:
    line = raw.rstrip("\n")
    if len(line) < 27:
        line = line.ljust(27)
    res_field = f"{int(resseq):4d}"
    icode_field = (str(icode or " ")[:1] or " ")
    return f"{line[:22]}{res_field}{icode_field}{line[27:]}"


def preprocess_pdb(
    pdb_text: str,
    *,
    chains: list[str] | None = None,
    strip_nonpositive_resseq: bool = False,
    renumber_resseq_from_1: bool = False,
) -> tuple[str, dict[str, list[dict[str, object]]]]:
    chain_set = set(chains) if chains is not None else None

    renumber_map: dict[str, dict[tuple[int, str], int]] = {}
    next_renum: dict[str, int] = {}
    residue_index: dict[str, int] = {}
    last_resseq: dict[str, int] = {}

    mapping: dict[str, list[dict[str, object]]] = {}
    seen_residue: set[tuple[str, int, str]] = set()

    out_lines: list[str] = []

    for raw in pdb_text.splitlines():
        rec = raw[:6].strip().upper()
        if rec not in {"ATOM", "HETATM", "TER"}:
            out_lines.append(raw)
            continue

        chain_id = raw[21:22].strip() or "_"
        if chain_set is not None and chain_id not in chain_set:
            out_lines.append(raw)
            continue

        resseq = _parse_int(raw[22:26])
        icode = raw[26:27].strip()

        if strip_nonpositive_resseq and rec in {"ATOM", "HETATM"} and int(resseq) <= 0:
            continue

        if rec == "TER":
            if renumber_resseq_from_1 and chain_id in last_resseq:
                out_lines.append(_rewrite_pdb_resseq_icode(raw, resseq=last_resseq[chain_id], icode=" "))
            else:
                out_lines.append(raw)
            continue

        key = (int(resseq), str(icode))
        new_resseq = int(resseq)
        new_icode = str(icode)
        if renumber_resseq_from_1:
            per_chain = renumber_map.setdefault(chain_id, {})
            if key not in per_chain:
                per_chain[key] = int(next_renum.get(chain_id, 1))
                next_renum[chain_id] = per_chain[key] + 1
            new_resseq = per_chain[key]
            new_icode = " "

        residue_key = (chain_id, int(resseq), str(icode))
        if residue_key not in seen_residue:
            seen_residue.add(residue_key)
            idx = int(residue_index.get(chain_id, 0)) + 1
            residue_index[chain_id] = idx
            mapping.setdefault(chain_id, []).append(
                {
                    "index": idx,
                    "original_resseq": int(resseq),
                    "original_icode": str(icode or ""),
                    "processed_resseq": int(new_resseq),
                    "processed_icode": str(new_icode or "").strip(),
                }
            )

        last_resseq[chain_id] = int(new_resseq)
        if renumber_resseq_from_1:
            out_lines.append(_rewrite_pdb_resseq_icode(raw, resseq=new_resseq, icode=new_icode))
        else:
            out_lines.append(raw)

    return ("\n".join(out_lines) + ("\n" if pdb_text.endswith("\n") else "")), mapping


def _ca_coords_by_chain(pdb_text: str, *, chains: list[str] | None = None) -> dict[str, dict[tuple[int, str], tuple[float, float, float]]]:
    residues = residues_by_chain(pdb_text, only_atom_records=True)
    chain_set = set(chains) if chains is not None else None
    out: dict[str, dict[tuple[int, str], tuple[float, float, float]]] = {}
    for chain_id, res_list in residues.items():
        if chain_set is not None and chain_id not in chain_set:
            continue
        coords: dict[tuple[int, str], tuple[float, float, float]] = {}
        for res in res_list:
            ca = None
            for atom in res.atoms:
                if atom.atom_name.strip().upper() == "CA":
                    ca = (atom.x, atom.y, atom.z)
                    break
            if ca is not None:
                coords[(res.resseq, res.icode)] = ca
        if coords:
            out[chain_id] = coords
    return out


def _match_ca_coords(
    ref_coords: dict[str, dict[tuple[int, str], tuple[float, float, float]]],
    mob_coords: dict[str, dict[tuple[int, str], tuple[float, float, float]]],
    *,
    chains: list[str] | None = None,
    include_positions: dict[str, set[tuple[int, str]]] | None = None,
) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    chain_order = chains or sorted(set(ref_coords) & set(mob_coords))
    ref: list[tuple[float, float, float]] = []
    mob: list[tuple[float, float, float]] = []
    for chain_id in chain_order:
        ref_chain = ref_coords.get(chain_id) or {}
        mob_chain = mob_coords.get(chain_id) or {}
        if not ref_chain or not mob_chain:
            continue
        for idx in sorted(set(ref_chain) & set(mob_chain)):
            allowed = include_positions.get(chain_id) if isinstance(include_positions, dict) else None
            if allowed is not None and idx not in allowed:
                continue
            ref.append(ref_chain[idx])
            mob.append(mob_chain[idx])
    return ref, mob


def _centroid(coords: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    if not coords:
        return (0.0, 0.0, 0.0)
    inv = 1.0 / float(len(coords))
    sx = sum(p[0] for p in coords)
    sy = sum(p[1] for p in coords)
    sz = sum(p[2] for p in coords)
    return (sx * inv, sy * inv, sz * inv)


def _center_coords(
    coords: list[tuple[float, float, float]],
    center: tuple[float, float, float],
) -> list[tuple[float, float, float]]:
    cx, cy, cz = center
    return [(x - cx, y - cy, z - cz) for (x, y, z) in coords]


def _covariance_matrix(
    mob: list[tuple[float, float, float]],
    ref: list[tuple[float, float, float]],
) -> list[list[float]]:
    sxx = syy = szz = sxy = sxz = syx = syz = szx = szy = 0.0
    for (mx, my, mz), (rx, ry, rz) in zip(mob, ref):
        sxx += mx * rx
        sxy += mx * ry
        sxz += mx * rz
        syx += my * rx
        syy += my * ry
        syz += my * rz
        szx += mz * rx
        szy += mz * ry
        szz += mz * rz
    return [
        [sxx, sxy, sxz],
        [syx, syy, syz],
        [szx, szy, szz],
    ]


def _quat_from_covariance(cov: list[list[float]]) -> tuple[float, float, float, float] | None:
    sxx, sxy, sxz = cov[0]
    syx, syy, syz = cov[1]
    szx, szy, szz = cov[2]
    n = [
        [sxx + syy + szz, syz - szy, szx - sxz, sxy - syx],
        [syz - szy, sxx - syy - szz, sxy + syx, szx + sxz],
        [szx - sxz, sxy + syx, -sxx + syy - szz, syz + szy],
        [sxy - syx, szx + sxz, syz + szy, -sxx - syy + szz],
    ]
    v = [1.0, 0.0, 0.0, 0.0]
    for _ in range(32):
        w0 = n[0][0] * v[0] + n[0][1] * v[1] + n[0][2] * v[2] + n[0][3] * v[3]
        w1 = n[1][0] * v[0] + n[1][1] * v[1] + n[1][2] * v[2] + n[1][3] * v[3]
        w2 = n[2][0] * v[0] + n[2][1] * v[1] + n[2][2] * v[2] + n[2][3] * v[3]
        w3 = n[3][0] * v[0] + n[3][1] * v[1] + n[3][2] * v[2] + n[3][3] * v[3]
        norm = math.sqrt(w0 * w0 + w1 * w1 + w2 * w2 + w3 * w3)
        if norm <= 0.0:
            return None
        v = [w0 / norm, w1 / norm, w2 / norm, w3 / norm]
    return (v[0], v[1], v[2], v[3])


def _rotation_from_quat(q: tuple[float, float, float, float]) -> list[list[float]]:
    w, x, y, z = q
    ww = w * w
    xx = x * x
    yy = y * y
    zz = z * z
    wx = w * x
    wy = w * y
    wz = w * z
    xy = x * y
    xz = x * z
    yz = y * z
    return [
        [ww + xx - yy - zz, 2.0 * (xy - wz), 2.0 * (xz + wy)],
        [2.0 * (xy + wz), ww - xx + yy - zz, 2.0 * (yz - wx)],
        [2.0 * (xz - wy), 2.0 * (yz + wx), ww - xx - yy + zz],
    ]


def _apply_rotation(
    vec: tuple[float, float, float],
    rot: list[list[float]],
) -> tuple[float, float, float]:
    x, y, z = vec
    return (
        rot[0][0] * x + rot[0][1] * y + rot[0][2] * z,
        rot[1][0] * x + rot[1][1] * y + rot[1][2] * z,
        rot[2][0] * x + rot[2][1] * y + rot[2][2] * z,
    )


def ca_rmsd(
    pdb_ref: str,
    pdb_mobile: str,
    *,
    chains: list[str] | None = None,
    include_positions: dict[str, set[tuple[int, str]]] | None = None,
) -> float | None:
    ref_coords = _ca_coords_by_chain(pdb_ref, chains=chains)
    mob_coords = _ca_coords_by_chain(pdb_mobile, chains=chains)
    ref, mob = _match_ca_coords(
        ref_coords,
        mob_coords,
        chains=chains,
        include_positions=include_positions,
    )
    # Two matched CA atoms are enough for a meaningful rigid-body superposition.
    # Keep returning None for 0/1 matched atoms because that alignment is too weak
    # to use as a structural gate.
    if len(ref) < 2 or len(mob) < 2:
        return None
    ref_center = _centroid(ref)
    mob_center = _centroid(mob)
    ref_c = _center_coords(ref, ref_center)
    mob_c = _center_coords(mob, mob_center)
    cov = _covariance_matrix(mob_c, ref_c)
    quat = _quat_from_covariance(cov)
    if quat is None:
        return None
    rot = _rotation_from_quat(quat)
    total = 0.0
    for r, m in zip(ref_c, mob_c):
        mx, my, mz = _apply_rotation(m, rot)
        dx = r[0] - mx
        dy = r[1] - my
        dz = r[2] - mz
        total += dx * dx + dy * dy + dz * dz
    return math.sqrt(total / float(len(ref_c)))


def _require_numpy():
    try:
        import numpy as np  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised in runtime only
        raise RuntimeError(
            "numpy is required for DSSP-based backbone filtering; install pipeline-mcp requirements"
        ) from exc
    return np


def _dssp_complete_backbone_residues(
    pdb_text: str,
    *,
    chains: list[str] | None = None,
) -> dict[str, list[dict[str, object]]]:
    residues = residues_by_chain(pdb_text, only_atom_records=True)
    chain_set = set(chains) if chains is not None else None
    out: dict[str, list[dict[str, object]]] = {}
    for chain_id, res_list in residues.items():
        if chain_set is not None and chain_id not in chain_set:
            continue
        chain_entries: list[dict[str, object]] = []
        for res in res_list:
            atoms_by_name: dict[str, tuple[float, float, float]] = {}
            for atom in res.atoms:
                atom_name = atom.atom_name.strip().upper()
                if atom_name in {"N", "CA", "C", "O"} and atom_name not in atoms_by_name:
                    atoms_by_name[atom_name] = (atom.x, atom.y, atom.z)
            if all(name in atoms_by_name for name in ("N", "CA", "C", "O")):
                chain_entries.append(
                    {
                        "key": (int(res.resseq), str(res.icode or "")),
                        "resname": str(res.resname or "").upper(),
                        "coords": [
                            atoms_by_name["N"],
                            atoms_by_name["CA"],
                            atoms_by_name["C"],
                            atoms_by_name["O"],
                        ],
                    }
                )
        if chain_entries:
            out[chain_id] = chain_entries
    return out


def _dssp_get_hydrogen_atom_position_numpy(coord):
    np = _require_numpy()
    vec_cn = coord[:, 1:, 0] - coord[:, :-1, 2]
    vec_cn = vec_cn / np.linalg.norm(vec_cn, axis=-1, keepdims=True)
    vec_can = coord[:, 1:, 0] - coord[:, 1:, 1]
    vec_can = vec_can / np.linalg.norm(vec_can, axis=-1, keepdims=True)
    vec_nh = vec_cn + vec_can
    vec_nh = vec_nh / np.linalg.norm(vec_nh, axis=-1, keepdims=True)
    return coord[:, 1:, 0] + 1.01 * vec_nh


def _dssp_get_hbond_map_numpy(
    coord,
    donor_mask=None,
    *,
    cutoff: float = -0.5,
    margin: float = 1.0,
):
    np = _require_numpy()
    org_shape = coord.shape
    if len(org_shape) == 3:
        coord = coord[None, ...]
    elif len(org_shape) != 4:
        raise ValueError("DSSP coordinates must have shape [L, atom, xyz] or [batch, L, atom, xyz]")
    batch, length, atom_count, _ = coord.shape
    if atom_count not in {4, 5}:
        raise ValueError("DSSP coordinates require 4 or 5 atoms per residue")
    hydrogen = coord[:, 1:, 4] if atom_count == 5 else _dssp_get_hydrogen_atom_position_numpy(coord)
    n_map = np.broadcast_to(coord[:, 1:, 0][:, :, None, :], (batch, length - 1, length - 1, 3))
    h_map = np.broadcast_to(hydrogen[:, :, None, :], (batch, length - 1, length - 1, 3))
    c_map = np.broadcast_to(coord[:, :-1, 2][:, None, :, :], (batch, length - 1, length - 1, 3))
    o_map = np.broadcast_to(coord[:, :-1, 3][:, None, :, :], (batch, length - 1, length - 1, 3))
    d_on = np.linalg.norm(o_map - n_map, axis=-1)
    d_ch = np.linalg.norm(c_map - h_map, axis=-1)
    d_oh = np.linalg.norm(o_map - h_map, axis=-1)
    d_cn = np.linalg.norm(c_map - n_map, axis=-1)
    energy = np.pad(
        0.084 * (1.0 / d_on + 1.0 / d_ch - 1.0 / d_oh - 1.0 / d_cn) * 332.0,
        ((0, 0), (1, 0), (0, 1)),
    )
    local_mask = ~np.eye(length, dtype=bool)
    local_mask &= ~np.diag(np.ones(length - 1, dtype=bool), k=-1)
    local_mask &= ~np.diag(np.ones(length - 2, dtype=bool), k=-2)
    donor_vector = (
        np.asarray(donor_mask, dtype=float)
        if donor_mask is not None
        else np.ones(length, dtype=float)
    )
    donor_grid = np.broadcast_to(donor_vector[:, None], (length, length))
    hbond_map = np.clip(cutoff - margin - energy, a_min=-margin, a_max=margin)
    hbond_map = (np.sin(hbond_map / margin * math.pi / 2.0) + 1.0) / 2.0
    hbond_map = hbond_map * np.broadcast_to(local_mask[None, :, :], (batch, length, length))
    hbond_map = hbond_map * np.broadcast_to(donor_grid[None, :, :], (batch, length, length))
    return np.squeeze(hbond_map, axis=0) if len(org_shape) == 3 else hbond_map


def _dssp_assign_c3_numpy(coord, donor_mask=None):
    np = _require_numpy()
    org_shape = coord.shape
    if len(org_shape) == 3:
        coord = coord[None, ...]
    hbmap = _dssp_get_hbond_map_numpy(coord, donor_mask=donor_mask)
    if hbmap.ndim == 2:
        hbmap = hbmap[None, ...]
    hbmap = np.swapaxes(hbmap, -1, -2)
    turn3 = np.diagonal(hbmap, offset=3, axis1=-2, axis2=-1) > 0.0
    turn4 = np.diagonal(hbmap, offset=4, axis1=-2, axis2=-1) > 0.0
    turn5 = np.diagonal(hbmap, offset=5, axis1=-2, axis2=-1) > 0.0
    h3 = np.pad(turn3[:, :-1] * turn3[:, 1:], ((0, 0), (1, 3)))
    h4 = np.pad(turn4[:, :-1] * turn4[:, 1:], ((0, 0), (1, 4)))
    h5 = np.pad(turn5[:, :-1] * turn5[:, 1:], ((0, 0), (1, 5)))
    helix4 = h4 + np.roll(h4, 1, 1) + np.roll(h4, 2, 1) + np.roll(h4, 3, 1)
    h3 = h3 * ~np.roll(helix4, -1, 1) * ~helix4
    h5 = h5 * ~np.roll(helix4, -1, 1) * ~helix4
    helix3 = h3 + np.roll(h3, 1, 1) + np.roll(h3, 2, 1)
    helix5 = h5 + np.roll(h5, 1, 1) + np.roll(h5, 2, 1) + np.roll(h5, 3, 1) + np.roll(h5, 4, 1)
    unfold = np.lib.stride_tricks.sliding_window_view(hbmap, 3, axis=-2)
    unfold = np.lib.stride_tricks.sliding_window_view(unfold, 3, axis=-2) > 0.0
    unfold_rev = np.swapaxes(unfold, 1, 2)
    p_bridge = (unfold[:, :, :, 0, 1] * unfold_rev[:, :, :, 1, 2]) + (
        unfold_rev[:, :, :, 0, 1] * unfold[:, :, :, 1, 2]
    )
    p_bridge = np.pad(p_bridge, ((0, 0), (1, 1), (1, 1)))
    a_bridge = (unfold[:, :, :, 1, 1] * unfold_rev[:, :, :, 1, 1]) + (
        unfold[:, :, :, 0, 2] * unfold_rev[:, :, :, 0, 2]
    )
    a_bridge = np.pad(a_bridge, ((0, 0), (1, 1), (1, 1)))
    ladder = (p_bridge + a_bridge) > 0
    helix = (helix3 + helix4 + helix5) > 0
    strand = np.any(ladder, axis=-1) | np.any(ladder, axis=-2)
    loop = (~helix) & (~strand)
    onehot = np.stack([loop, helix, strand], axis=-1)
    if len(org_shape) == 3:
        onehot = np.squeeze(onehot, axis=0)
    return onehot


def dssp_non_loop_positions_by_chain(
    pdb_text: str,
    *,
    chains: list[str] | None = None,
) -> dict[str, set[tuple[int, str]]]:
    np = _require_numpy()
    backbone = _dssp_complete_backbone_residues(pdb_text, chains=chains)
    out: dict[str, set[tuple[int, str]]] = {}
    for chain_id, residues in backbone.items():
        if len(residues) < 2:
            continue
        coords = np.asarray([entry["coords"] for entry in residues], dtype=float)
        donor_mask = np.asarray(
            [0.0 if str(entry.get("resname") or "").upper() == "PRO" else 1.0 for entry in residues],
            dtype=float,
        )
        onehot = _dssp_assign_c3_numpy(coords, donor_mask=donor_mask)
        if onehot.ndim != 2 or onehot.shape[-1] != 3:
            continue
        non_loop: set[tuple[int, str]] = set()
        for idx, entry in enumerate(residues):
            if bool(onehot[idx][1]) or bool(onehot[idx][2]):
                key = entry.get("key")
                if isinstance(key, tuple) and len(key) == 2:
                    non_loop.add((int(key[0]), str(key[1] or "")))
        if non_loop:
            out[chain_id] = non_loop
    return out


_VDW_RADII = {
    "H": 1.2,
    "C": 1.7,
    "N": 1.55,
    "O": 1.52,
    "S": 1.8,
    "P": 1.8,
    "SE": 1.9,
    "FE": 1.8,
    "ZN": 1.39,
    "MG": 1.73,
    "CA": 1.94,
}

# Max ASA values (Tien et al., 2013) in Å^2
_RES_MAX_ASA = {
    "A": 121.0,
    "R": 265.0,
    "N": 187.0,
    "D": 187.0,
    "C": 148.0,
    "Q": 214.0,
    "E": 214.0,
    "G": 97.0,
    "H": 216.0,
    "I": 195.0,
    "L": 191.0,
    "K": 230.0,
    "M": 203.0,
    "F": 228.0,
    "P": 154.0,
    "S": 143.0,
    "T": 163.0,
    "W": 264.0,
    "Y": 255.0,
    "V": 165.0,
}


def _vdw_radius(element: str) -> float:
    key = (element or "").strip().upper()
    if not key:
        return 1.7
    if key in _VDW_RADII:
        return _VDW_RADII[key]
    if len(key) > 1 and key[:1] in _VDW_RADII:
        return _VDW_RADII[key[:1]]
    return 1.7


@lru_cache(maxsize=8)
def _unit_sphere_points(count: int) -> list[tuple[float, float, float]]:
    points: list[tuple[float, float, float]] = []
    n = max(1, int(count))
    offset = 2.0 / float(n)
    inc = math.pi * (3.0 - math.sqrt(5.0))
    for i in range(n):
        y = (i * offset) - 1.0 + (offset / 2.0)
        r = math.sqrt(max(0.0, 1.0 - y * y))
        phi = i * inc
        x = math.cos(phi) * r
        z = math.sin(phi) * r
        points.append((x, y, z))
    return points


def residue_sasa_by_chain(
    pdb_text: str,
    *,
    chains: list[str] | None = None,
    probe_radius: float = 1.4,
    points_per_atom: int = 60,
) -> dict[str, dict[int, dict[str, float | str]]]:
    residues = residues_by_chain(pdb_text, only_atom_records=True)
    if chains is not None:
        residues = {k: v for k, v in residues.items() if k in set(chains)}

    atoms: list[tuple[float, float, float, float, str, int, str]] = []
    for chain_id, res_list in residues.items():
        for res in res_list:
            for atom in res.atoms:
                if not _is_heavy(atom):
                    continue
                radius = _vdw_radius(atom.element)
                atoms.append((atom.x, atom.y, atom.z, radius, chain_id, res.index, res.resname))

    if not atoms:
        return {}

    cell_size = 4.0 + float(probe_radius)
    grid: dict[tuple[int, int, int], list[int]] = {}
    for idx, (x, y, z, _r, _c, _i, _rn) in enumerate(atoms):
        cell = (int(x // cell_size), int(y // cell_size), int(z // cell_size))
        grid.setdefault(cell, []).append(idx)

    points = _unit_sphere_points(points_per_atom)
    residue_sasa: dict[str, dict[int, dict[str, float | str]]] = {}

    for idx, (x, y, z, radius, chain_id, res_idx, resname) in enumerate(atoms):
        cell = (int(x // cell_size), int(y // cell_size), int(z // cell_size))
        neighbors: list[tuple[float, float, float, float]] = []
        ri = radius + probe_radius
        ri2 = ri * ri
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for j in grid.get((cell[0] + dx, cell[1] + dy, cell[2] + dz), []):
                        if j == idx:
                            continue
                        xj, yj, zj, rj, *_ = atoms[j]
                        rj_probe = rj + probe_radius
                        # prune far neighbors
                        max_d = ri + rj_probe
                        dxj = x - xj
                        dyj = y - yj
                        dzj = z - zj
                        if (dxj * dxj + dyj * dyj + dzj * dzj) <= max_d * max_d:
                            neighbors.append((xj, yj, zj, rj_probe))

        accessible = 0
        for px, py, pz in points:
            sx = x + px * ri
            sy = y + py * ri
            sz = z + pz * ri
            occluded = False
            for xj, yj, zj, rj_probe in neighbors:
                dxj = sx - xj
                dyj = sy - yj
                dzj = sz - zj
                if (dxj * dxj + dyj * dyj + dzj * dzj) < (rj_probe * rj_probe):
                    occluded = True
                    break
            if not occluded:
                accessible += 1

        sasa = (accessible / float(len(points))) * (4.0 * math.pi * ri2)
        chain_payload = residue_sasa.setdefault(chain_id, {})
        entry = chain_payload.get(res_idx)
        if not isinstance(entry, dict):
            entry = {"resname": resname, "sasa": 0.0}
            chain_payload[res_idx] = entry
        entry["sasa"] = float(entry.get("sasa") or 0.0) + sasa

    return residue_sasa


def surface_positions_by_chain(
    pdb_text: str,
    *,
    chains: list[str] | None = None,
    probe_radius: float = 1.4,
    points_per_atom: int = 60,
    min_rel: float = 0.2,
    min_abs: float = 10.0,
) -> tuple[dict[str, list[int]], dict[str, dict[int, dict[str, float | str]]]]:
    sasa_by_chain = residue_sasa_by_chain(
        pdb_text,
        chains=chains,
        probe_radius=probe_radius,
        points_per_atom=points_per_atom,
    )
    surface: dict[str, list[int]] = {}
    for chain_id, residues in sasa_by_chain.items():
        surface_positions: list[int] = []
        for res_idx, payload in residues.items():
            resname = str(payload.get("resname") or "").upper()
            aa = _AA3_TO_AA1.get(resname, "X")
            max_asa = _RES_MAX_ASA.get(aa)
            sasa_val = float(payload.get("sasa") or 0.0)
            rel = sasa_val / max_asa if max_asa else None
            payload["rel_sasa"] = float(rel) if rel is not None else None
            if rel is not None:
                if rel >= float(min_rel) or sasa_val >= float(min_abs):
                    surface_positions.append(int(res_idx))
            elif sasa_val >= float(min_abs):
                surface_positions.append(int(res_idx))
        if surface_positions:
            surface[chain_id] = sorted(set(surface_positions))
    return surface, sasa_by_chain
