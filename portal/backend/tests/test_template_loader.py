import pytest
from app.workflow import template_loader


def test_load_rapid_v1_returns_ordered_steps():
    tpl = template_loader.load_template("rapid_v1")
    assert tpl["template_key"] == "rapid_v1"
    steps = template_loader.ordered_steps(tpl)
    names = [s["step_name"] for s in steps]
    assert names == [
        "FASTA/PDB Input", "MSA Search", "Conservation Mask",
        "ProteinMPNN Design", "SoluProt Filter", "Structure Validation", "Report Export",
    ]


def test_unknown_template_raises():
    with pytest.raises(KeyError):
        template_loader.load_template("does_not_exist")
