from app import chaining


def test_compat_endpoint_returns_graph(monkeypatch):
    # The router handler is a thin wrapper over chaining.compat_graph(); assert
    # the payload it returns is the derived graph with nodes/edges/examples.
    from app.routers import chains
    payload = chains.get_compat(current_user=object())
    assert payload == chaining.compat_graph()
    assert payload["nodes"] and payload["edges"] and payload["examples"]
