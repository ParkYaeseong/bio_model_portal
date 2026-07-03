from app.workflow import conservation


def test_fully_conserved_column_is_in_all_tiers():
    msa = ["ACDE", "ACDE", "ACDE"]
    mask = conservation.fixed_positions(msa, tiers=[30, 50, 70])
    assert mask["70"] == [1, 2, 3, 4]
    assert mask["30"] == [1, 2, 3, 4]


def test_variable_column_excluded_from_high_tier():
    msa = ["ACDE", "AGDE", "AHDE"]  # column 2 varies (C/G/H -> 33% identity)
    mask = conservation.fixed_positions(msa, tiers=[30, 50, 70])
    assert 2 not in mask["70"]
    assert 2 in mask["30"]
    assert mask["70"] == [1, 3, 4]


def test_empty_msa_returns_empty_tiers():
    assert conservation.fixed_positions([], tiers=[30, 50, 70]) == {"30": [], "50": [], "70": []}
