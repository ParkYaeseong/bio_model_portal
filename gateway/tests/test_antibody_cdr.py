from prep import antibody_cdr


_AA3 = {
    "A": "ALA", "R": "ARG", "N": "ASN", "D": "ASP", "C": "CYS", "Q": "GLN",
    "E": "GLU", "G": "GLY", "H": "HIS", "I": "ILE", "L": "LEU", "K": "LYS",
    "M": "MET", "F": "PHE", "P": "PRO", "S": "SER", "T": "THR", "W": "TRP",
    "Y": "TYR", "V": "VAL",
}


def _pdb_for_sequence(sequence: str, chain: str = "H") -> str:
    lines = []
    for i, aa in enumerate(sequence, start=1):
        resname = _AA3[aa]
        lines.append(
            f"ATOM  {i:5d}  CA  {resname} {chain}{i:4d}    "
            f"{float(i):8.3f}{0.0:8.3f}{0.0:8.3f}  1.00  0.00           C"
        )
    return "\n".join(lines) + "\n"


def _domain_no_gaps(sequence: str, *, chain_type: str = "H") -> dict:
    """A domain response where IMGT position == sequence position 1:1 (no
    alignment gaps) -- the common case and the simplest to hand-verify."""
    return {
        "chain_type": chain_type,
        "score": 30.0,
        "query_start": 0,
        "query_end": len(sequence) - 1,
        "error": None,
        "scheme": "imgt",
        "numbering": [[[i + 1, " "], aa] for i, aa in enumerate(sequence)],
    }


def _domain_with_a_gap(sequence: str, *, gap_after_index: int, chain_type: str = "H") -> dict:
    """Insert one alignment gap right after `gap_after_index` (0-based) --
    every real residue after the gap gets an IMGT position one higher than
    its plain sequence index, exercising the gap-skip logic."""
    numbering = []
    imgt_pos = 1
    for idx, aa in enumerate(sequence):
        numbering.append([[imgt_pos, " "], aa])
        imgt_pos += 1
        if idx == gap_after_index:
            numbering.append([[imgt_pos, " "], "-"])
            imgt_pos += 1
    return {
        "chain_type": chain_type,
        "score": 30.0,
        "query_start": 0,
        "query_end": len(sequence) - 1,
        "error": None,
        "scheme": "imgt",
        "numbering": numbering,
    }


# 130 residues: enough room for CDR-H1 (27-38), CDR-H2 (56-65), CDR-H3
# (105-117) to all land inside a plain 1:1 (no-gap) numbering.
_VH_LIKE = "A" * 130


def test_cdr_only_mode_leaves_only_cdr_residues_mutable(monkeypatch):
    domain = _domain_no_gaps(_VH_LIKE)
    monkeypatch.setattr(antibody_cdr, "_call_anarcii", lambda seq: {"query": domain})

    pdb_text = _pdb_for_sequence(_VH_LIKE, chain="H")
    fixed = antibody_cdr.compute_antibody_fixed_positions(pdb_text, include_framework=False)

    fixed_set = set(fixed["H"])
    expected_cdr = set(range(27, 39)) | set(range(56, 66)) | set(range(105, 118))
    mutable_set = set(range(1, 131)) - fixed_set

    assert mutable_set == expected_cdr
    assert 1 in fixed_set  # framework residue stays fixed
    assert 50 in fixed_set  # between CDR1 and CDR2, still framework


def test_include_framework_widens_the_mutable_set(monkeypatch):
    domain = _domain_no_gaps(_VH_LIKE)
    monkeypatch.setattr(antibody_cdr, "_call_anarcii", lambda seq: {"query": domain})
    pdb_text = _pdb_for_sequence(_VH_LIKE, chain="H")

    cdr_only = antibody_cdr.compute_antibody_fixed_positions(pdb_text, include_framework=False)
    with_fr = antibody_cdr.compute_antibody_fixed_positions(pdb_text, include_framework=True)

    assert len(with_fr["H"]) < len(cdr_only["H"])
    # A plain framework residue becomes mutable once FR is included.
    assert 50 in cdr_only["H"]
    assert 50 not in with_fr["H"]


def test_vhh_hallmark_positions_stay_fixed_even_with_framework_included(monkeypatch):
    domain = _domain_no_gaps(_VH_LIKE)
    monkeypatch.setattr(antibody_cdr, "_call_anarcii", lambda seq: {"query": domain})
    pdb_text = _pdb_for_sequence(_VH_LIKE, chain="H")

    with_fr = antibody_cdr.compute_antibody_fixed_positions(pdb_text, include_framework=True)

    # 37 falls inside CDR-H1 (27-38) itself, so it's mutable via the CDR path
    # regardless of the hallmark check -- that check only guards the
    # framework-only branch, matching antigen_pipeline's own semantics
    # (framework_contact excludes hallmarks, is_cdr does not). Only 44/45/47
    # are pure framework hallmark positions this test can check.
    for hallmark in (44, 45, 47):
        assert hallmark in with_fr["H"]


def test_cysteines_are_never_mutable_even_inside_a_cdr(monkeypatch):
    seq = list(_VH_LIKE)
    seq[29] = "C"  # 0-based index 29 -> IMGT position 30, inside CDR-H1 (27-38)
    seq = "".join(seq)
    domain = _domain_no_gaps(seq)
    monkeypatch.setattr(antibody_cdr, "_call_anarcii", lambda s: {"query": domain})

    pdb_text = _pdb_for_sequence(seq, chain="H")
    fixed = antibody_cdr.compute_antibody_fixed_positions(pdb_text, include_framework=False)

    assert 30 in fixed["H"]


def test_alignment_gaps_do_not_shift_the_mapping_back_to_pdb_residues(monkeypatch):
    # A gap right before CDR-H1 (after index 20) shifts every later IMGT
    # position up by one relative to a no-gap domain; the function must still
    # land on the correct real residues, not off-by-one ones.
    domain = _domain_with_a_gap(_VH_LIKE, gap_after_index=20)
    monkeypatch.setattr(antibody_cdr, "_call_anarcii", lambda seq: {"query": domain})
    pdb_text = _pdb_for_sequence(_VH_LIKE, chain="H")

    fixed = antibody_cdr.compute_antibody_fixed_positions(pdb_text, include_framework=False)
    mutable_set = set(range(1, 131)) - set(fixed["H"])

    # The gap sits before every CDR, so each one shifts down by exactly one
    # PDB/sequence position relative to its plain IMGT range: CDR-H1 27-38 ->
    # residues 26-37, CDR-H3 105-117 -> residues 104-116 (not the unshifted
    # 27-38 / 105-117 -- proof the gap is actually being skipped, not just
    # ignored coincidentally).
    assert mutable_set & set(range(105, 118)) == set(range(105, 117))  # NOT the full unshifted range (missing 117)
    assert 104 in mutable_set and 117 not in mutable_set
    assert set(range(26, 38)) <= mutable_set  # CDR-H1 shifted down by one
    assert 38 not in mutable_set  # the unshifted upper bound is now just outside


def test_mutable_include_widens_and_exclude_narrows(monkeypatch):
    domain = _domain_no_gaps(_VH_LIKE)
    monkeypatch.setattr(antibody_cdr, "_call_anarcii", lambda seq: {"query": domain})
    pdb_text = _pdb_for_sequence(_VH_LIKE, chain="H")

    baseline = antibody_cdr.compute_antibody_fixed_positions(pdb_text)
    widened = antibody_cdr.compute_antibody_fixed_positions(pdb_text, mutable_include="H1")
    narrowed = antibody_cdr.compute_antibody_fixed_positions(
        pdb_text, mutable_exclude=",".join(f"H{p}" for p in range(27, 39))
    )

    assert 1 in baseline["H"] and 1 not in widened["H"]
    assert set(range(27, 39)) <= set(narrowed["H"])  # excluded CDR1 residues now forced fixed


def test_a_chain_anarcii_cannot_number_is_fixed_in_its_entirety(monkeypatch):
    monkeypatch.setattr(
        antibody_cdr, "_call_anarcii",
        lambda seq: (_ for _ in ()).throw(RuntimeError("not an antibody")),
    )

    pdb_text = _pdb_for_sequence("ACDEFGHIKL", chain="X")
    fixed = antibody_cdr.compute_antibody_fixed_positions(pdb_text)

    assert fixed["X"] == list(range(1, 11))


def test_anarcii_reported_error_for_a_domain_is_treated_like_no_domain(monkeypatch):
    monkeypatch.setattr(
        antibody_cdr, "_call_anarcii",
        lambda seq: {"query": {"error": "could not align", "chain_type": None, "numbering": []}},
    )

    pdb_text = _pdb_for_sequence("ACDEFGHIKL", chain="X")
    fixed = antibody_cdr.compute_antibody_fixed_positions(pdb_text)

    assert fixed["X"] == list(range(1, 11))


def test_build_input_antibody_mode_computes_fixed_positions(monkeypatch):
    from prep import proteinmpnn

    domain = _domain_no_gaps(_VH_LIKE)
    monkeypatch.setattr(antibody_cdr, "_call_anarcii", lambda seq: {"query": domain})

    pdb_text = _pdb_for_sequence(_VH_LIKE, chain="H")
    out = proteinmpnn.build_input({"pdb_content": pdb_text, "design_mode": "antibody"})

    fixed = out["fixed_positions"]["H"]
    assert 1 in fixed  # framework, fixed
    assert 30 not in fixed  # inside CDR-H1, mutable


def test_build_input_antibody_mode_respects_include_framework(monkeypatch):
    from prep import proteinmpnn

    domain = _domain_no_gaps(_VH_LIKE)
    monkeypatch.setattr(antibody_cdr, "_call_anarcii", lambda seq: {"query": domain})

    pdb_text = _pdb_for_sequence(_VH_LIKE, chain="H")
    out = proteinmpnn.build_input(
        {"pdb_content": pdb_text, "design_mode": "antibody", "include_framework": "true"}
    )

    assert 50 not in out["fixed_positions"]["H"]  # framework, now mutable too


def test_build_input_manual_mode_is_unaffected_by_antibody_fields(monkeypatch):
    from prep import proteinmpnn

    def _must_not_be_called(seq):
        raise AssertionError("ANARCII must not be called outside antibody mode")

    monkeypatch.setattr(antibody_cdr, "_call_anarcii", _must_not_be_called)

    pdb_text = _pdb_for_sequence(_VH_LIKE, chain="H")
    out = proteinmpnn.build_input({"pdb_content": pdb_text, "fixed_positions": "H1-5"})

    assert out["fixed_positions"]["H"] == [1, 2, 3, 4, 5]
