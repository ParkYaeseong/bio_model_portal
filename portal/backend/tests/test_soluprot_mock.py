from app.workflow import soluprot_mock


def test_score_is_deterministic_and_bounded():
    a = soluprot_mock.score("MKTAYIAKQR")
    b = soluprot_mock.score("MKTAYIAKQR")
    assert a == b
    assert 0.0 <= a <= 1.0


def test_filter_top_k_sorts_desc_and_truncates():
    candidates = [
        {"id": "s1", "sequence": "AAAA"},
        {"id": "s2", "sequence": "MKTAYIAKQR"},
        {"id": "s3", "sequence": "WWWWWWWW"},
    ]
    top = soluprot_mock.filter_top_k(candidates, top_k=2)
    assert len(top) == 2
    assert top[0]["soluprot_score"] >= top[1]["soluprot_score"]
    assert "soluprot_score" in top[0]
