import pytest
from prep import sequence

def test_valid_sequence_passes():
    out = sequence.build_bioemu_input({"sequence": "MKTAYIAKQR", "num_samples": 1})
    assert out["sequence"] == "MKTAYIAKQR" and out["num_samples"] == 1
    assert "input_archive" not in out

def test_bad_sequence_raises_with_position():
    with pytest.raises(ValueError) as e:
        sequence.build_bioemu_input({"sequence": "MKX1", "num_samples": 1})
    msg = str(e.value)
    assert "BioEmu" in msg and ("position" in msg.lower() or "3" in msg)

def test_empty_sequence_raises():
    with pytest.raises(ValueError):
        sequence.build_bioemu_input({"sequence": "", "num_samples": 1})
