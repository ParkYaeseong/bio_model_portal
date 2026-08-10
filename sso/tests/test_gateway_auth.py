from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import create_app


@pytest.fixture
def app():
    return create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "KBF_OIDC_ISSUER": "https://sso.example.test/realms/kbf",
            "KBF_OIDC_CLIENT_ID": "rbpfinder",
            "UPSTREAM_BASE_URL": "http://rbpfinder-api:8000",
        }
    )


@pytest.fixture
def client(app):
    return app.test_client()


def _login_session(client, claims: dict | None = None):
    with client.session_transaction() as session:
        session["kbf_user"] = {
            "sub": "user-1",
            "email": "user@example.test",
            "name": "User Example",
            "claims": claims or {},
        }


def test_root_redirects_to_login_when_session_is_missing(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_auth_verify_redirects_to_login_with_forwarded_path_when_session_is_missing(app):
    response = app.handle_request(
        "GET",
        "/auth/verify",
        headers={
            "host": "rbpfinder.k-biofoundrycopilot.duckdns.org",
            "x-forwarded-uri": "/index.html",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/login?next=%2Findex.html"


def test_auth_verify_returns_ok_when_session_is_present(app, client):
    _login_session(client)

    response = app.handle_request(
        "GET",
        "/auth/verify",
        headers={
            "host": "rbpfinder.k-biofoundrycopilot.duckdns.org",
            "cookie": client._cookie_header(),
            "x-forwarded-uri": "/",
        },
    )

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "ok"


def test_auth_verify_emits_identity_headers(app, client):
    _login_session(client)

    response = app.handle_request(
        "GET",
        "/auth/verify",
        headers={
            "host": "rbpfinder.k-biofoundrycopilot.duckdns.org",
            "cookie": client._cookie_header(),
            "x-forwarded-uri": "/",
        },
    )

    assert response.status_code == 200
    assert response.headers["X-KBF-User"] == "user-1"
    assert response.headers["X-KBF-Email"] == "user@example.test"
    assert response.headers["X-KBF-Name"] == "User%20Example"


def test_authenticated_api_request_proxies_to_upstream_backend(client, monkeypatch: pytest.MonkeyPatch):
    _login_session(client)

    def fake_request(method, url, **kwargs):
        assert method == "GET"
        assert url == "http://rbpfinder-api:8000/projects"
        return SimpleNamespace(
            status_code=200,
            content=b"[]",
            headers={"Content-Type": "application/json"},
        )

    monkeypatch.setattr("app.requests.request", fake_request)

    response = client.get("/api/projects", follow_redirects=False)

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "[]"


def test_proxy_drops_client_supplied_identity_headers(app, client, monkeypatch: pytest.MonkeyPatch):
    """A client cannot spoof identity by sending X-KBF-* headers: the proxy
    strips inbound copies and injects the authoritative session identity."""
    _login_session(client)
    captured: dict = {}

    def fake_request(method, url, **kwargs):
        captured["headers"] = kwargs.get("headers", {})
        return SimpleNamespace(status_code=200, content=b"ok", headers={})

    monkeypatch.setattr("app.requests.request", fake_request)

    response = app.handle_request(
        "GET",
        "/api/projects",
        headers={
            "host": "rbpfinder.k-biofoundrycopilot.duckdns.org",
            "cookie": client._cookie_header(),
            "x-kbf-user": "attacker",       # forged
            "x-kbf-auth": "forged-secret",  # forged
        },
    )

    assert response.status_code == 200
    sent = {k.lower(): v for k, v in captured["headers"].items()}
    assert sent.get("x-kbf-user") == "user-1"          # authoritative session sub
    assert "x-kbf-auth" not in sent                    # no secret configured -> not injected, forged dropped


def test_auth_verify_marks_the_kbf_admin_role_as_admin(app, client):
    _login_session(client, claims={"realm_access": {"roles": ["kbf-admin"]}})

    response = app.handle_request(
        "GET",
        "/auth/verify",
        headers={
            "host": "rbpfinder.k-biofoundrycopilot.duckdns.org",
            "cookie": client._cookie_header(),
            "x-forwarded-uri": "/",
        },
    )

    assert response.status_code == 200
    assert response.headers["X-KBF-Admin"] == "true"


def test_auth_verify_omits_the_admin_header_for_everyone_else(app, client):
    _login_session(client, claims={"realm_access": {"roles": ["some-other-role"]}})

    response = app.handle_request(
        "GET",
        "/auth/verify",
        headers={
            "host": "rbpfinder.k-biofoundrycopilot.duckdns.org",
            "cookie": client._cookie_header(),
            "x-forwarded-uri": "/",
        },
    )

    assert response.status_code == 200
    assert "X-KBF-Admin" not in response.headers


def test_a_client_supplied_admin_header_is_stripped_before_proxying(app, client, monkeypatch: pytest.MonkeyPatch):
    _login_session(client)
    captured: dict = {}

    def fake_request(method, url, **kwargs):
        captured["headers"] = kwargs.get("headers", {})
        return SimpleNamespace(status_code=200, content=b"ok", headers={})

    monkeypatch.setattr("app.requests.request", fake_request)

    response = app.handle_request(
        "GET",
        "/api/projects",
        headers={
            "host": "rbpfinder.k-biofoundrycopilot.duckdns.org",
            "cookie": client._cookie_header(),
            "x-kbf-admin": "true",  # forged
        },
    )

    assert response.status_code == 200
    sent = {k.lower(): v for k, v in captured["headers"].items()}
    assert "x-kbf-admin" not in sent


def test_logout_bridge_clears_session_cookie_and_returns_completion_html(client):
    _login_session(client)

    response = client.get(
        "/logout-bridge.html?parent_origin=https://k-biofoundrycopilot.duckdns.org&request_id=req-1"
    )

    assert response.status_code == 200
    assert "logout-bridge:ok" in response.get_data(as_text=True)

    with client.session_transaction() as session:
        assert "kbf_user" not in session


def test_dev_gateway_rejects_authenticated_user_without_required_role():
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "KBF_OIDC_ISSUER": "https://sso.example.test/realms/kbf",
            "KBF_OIDC_CLIENT_ID": "rbpfinder-dev",
            "KBF_REQUIRE_ROLE": "rbpfinder-admin",
            "UPSTREAM_BASE_URL": "http://rbpfinder-api:8000",
        }
    )
    client = app.test_client()
    _login_session(
        client,
        claims={"resource_access": {"rbpfinder-dev": {"roles": ["viewer"]}}},
    )

    response = client.get("/api/projects", follow_redirects=False)

    assert response.status_code == 403
    assert response.get_data(as_text=True) == "forbidden"
