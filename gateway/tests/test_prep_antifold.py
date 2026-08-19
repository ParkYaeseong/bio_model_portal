import base64

import pytest

from prep import antifold

_AA3 = {
    "A": "ALA", "R": "ARG", "N": "ASN", "D": "ASP", "C": "CYS", "Q": "GLN",
    "E": "GLU", "G": "GLY", "H": "HIS", "I": "ILE", "L": "LEU", "K": "LYS",
    "M": "MET", "F": "PHE", "P": "PRO", "S": "SER", "T": "THR", "W": "TRP",
    "Y": "TYR", "V": "VAL",
}

_VH_LIKE = "A" * 20
_VL_LIKE = "A" * 20


def _chain_lines(sequence: str, chain: str, start: int = 1) -> list[str]:
    lines = []
    for i, aa in enumerate(sequence, start=start):
        resname = _AA3[aa]
        lines.append(
            f"ATOM  {i:5d}  CA  {resname} {chain}{i:4d}    "
            f"{float(i):8.3f}{0.0:8.3f}{0.0:8.3f}  1.00  0.00           C"
        )
    return lines


def _paired_pdb(heavy: str = _VH_LIKE, light: str = _VL_LIKE) -> str:
    lines = _chain_lines(heavy, "H") + _chain_lines(light, "L")
    return "\n".join(lines) + "\n"


def _domain_no_gaps(sequence: str, *, chain_type: str = "H") -> dict:
    return {
        "chain_type": chain_type,
        "score": 30.0,
        "query_start": 0,
        "query_end": len(sequence) - 1,
        "error": None,
        "scheme": "imgt",
        # Shift by +5 so the renumbered positions are visibly different from
        # the original 1-based PDB numbering -- proof the rewrite actually
        # happened, not a coincidence of identical ranges.
        "numbering": [[[i + 6, " "], aa] for i, aa in enumerate(sequence)],
    }


def _mock_anarcii(monkeypatch, chain_type_by_call=None):
    calls = []

    def _fake(sequence):
        calls.append(sequence)
        chain_type = "H"
        if chain_type_by_call:
            chain_type = chain_type_by_call(len(calls) - 1, sequence)
        return {"query": _domain_no_gaps(sequence, chain_type=chain_type)}

    monkeypatch.setattr(antifold, "_call_anarcii", _fake)
    return calls


def _resseqs_for_chain(pdb_text: str, chain: str) -> list[int]:
    out = []
    for line in pdb_text.splitlines():
        if line.startswith("ATOM") and line[21:22] == chain:
            out.append(int(line[22:26]))
    return out


def test_defaults_to_heavy_light_h_and_l(monkeypatch):
    _mock_anarcii(monkeypatch)
    out = antifold.build_input({"pdb_content": _paired_pdb()})
    assert out["heavy_chain"] == "H"
    assert out["light_chain"] == "L"
    assert "nanobody_chain" not in out


def test_renumbers_both_chains_to_imgt(monkeypatch):
    _mock_anarcii(monkeypatch)
    out = antifold.build_input({"pdb_content": _paired_pdb()})
    pdb = base64.b64decode(out["pdb_base64"]).decode("utf-8")
    # Original PDB numbering was 1..20; the mocked domain shifts every
    # position by +6, so the rewritten PDB must show 6..25, not 1..20.
    assert _resseqs_for_chain(pdb, "H") == list(range(6, 26))
    assert _resseqs_for_chain(pdb, "L") == list(range(6, 26))


def test_explicit_chain_ids_are_respected(monkeypatch):
    _mock_anarcii(monkeypatch)
    out = antifold.build_input({"pdb_content": _paired_pdb("A" * 20, "A" * 20), "heavy_chain": "H", "light_chain": "L"})
    assert out["heavy_chain"] == "H"
    assert out["light_chain"] == "L"


def test_nanobody_chain_suppresses_heavy_light(monkeypatch):
    _mock_anarcii(monkeypatch)
    pdb = "\n".join(_chain_lines(_VH_LIKE, "H")) + "\n"
    out = antifold.build_input({"pdb_content": pdb, "nanobody_chain": "H"})
    assert out["nanobody_chain"] == "H"
    assert "heavy_chain" not in out
    assert "light_chain" not in out


def test_a_chain_anarcii_cannot_number_is_left_unchanged(monkeypatch):
    monkeypatch.setattr(
        antifold, "_call_anarcii",
        lambda seq: (_ for _ in ()).throw(RuntimeError("not an antibody")),
    )
    pdb_in = _paired_pdb()
    out = antifold.build_input({"pdb_content": pdb_in})
    pdb_out = base64.b64decode(out["pdb_base64"]).decode("utf-8")
    # ANARCII failed for both chains, so the structure passes through as-is.
    assert _resseqs_for_chain(pdb_out, "H") == list(range(1, 21))
    assert _resseqs_for_chain(pdb_out, "L") == list(range(1, 21))


def test_antigen_chain_is_passed_through_only_when_set(monkeypatch):
    _mock_anarcii(monkeypatch)
    out = antifold.build_input({"pdb_content": _paired_pdb()})
    assert "antigen_chain" not in out
    out = antifold.build_input({"pdb_content": _paired_pdb(), "antigen_chain": "Y"})
    assert out["antigen_chain"] == "Y"


def test_regions_default_to_cdr_only(monkeypatch):
    _mock_anarcii(monkeypatch)
    out = antifold.build_input({"pdb_content": _paired_pdb()})
    assert out["regions"] == "CDR1 CDR2 CDR3H"


def test_framework_region_selection_is_accepted(monkeypatch):
    _mock_anarcii(monkeypatch)
    out = antifold.build_input({"pdb_content": _paired_pdb(), "regions": "FWH FWL"})
    assert out["regions"] == "FWH FWL"


def test_unknown_region_token_is_rejected(monkeypatch):
    _mock_anarcii(monkeypatch)
    with pytest.raises(ValueError):
        antifold.build_input({"pdb_content": _paired_pdb(), "regions": "CDR9"})


def test_numeric_and_string_fields_pass_through(monkeypatch):
    _mock_anarcii(monkeypatch)
    out = antifold.build_input(
        {
            "pdb_content": _paired_pdb(),
            "num_seq_per_target": "4",
            "sampling_temp": "0.35",
            "seed": "7",
        }
    )
    assert out["num_seq_per_target"] == 4
    assert out["sampling_temp"] == "0.35"
    assert out["seed"] == 7


def test_bool_flags_from_select_strings(monkeypatch):
    _mock_anarcii(monkeypatch)
    out = antifold.build_input(
        {"pdb_content": _paired_pdb(), "custom_chain_mode": "true", "esm_if1_mode": "false"}
    )
    assert out["custom_chain_mode"] is True
    assert out["esm_if1_mode"] is False
