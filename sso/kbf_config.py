from __future__ import annotations

import os
from dataclasses import dataclass


def _env(key: str, default: str | None = None) -> str | None:
    value = os.environ.get(key)
    if value is None:
        return default
    stripped = value.strip()
    return stripped if stripped else default


@dataclass(frozen=True)
class KbfSettings:
    testing: bool
    secret_key: str
    oidc_issuer: str
    oidc_client_id: str
    oidc_client_secret: str | None
    oidc_scopes: str
    upstream_base_url: str
    session_cookie_name: str
    secure_cookies: bool
    required_role: str | None
    forward_auth_secret: str | None


def load_settings(overrides: dict | None = None) -> KbfSettings:
    overrides = overrides or {}

    testing = bool(overrides.get("TESTING", False))
    secret_key = str(
        overrides.get("SECRET_KEY")
        or overrides.get("KBF_SESSION_SECRET")
        or _env("KBF_SESSION_SECRET")
        or "change-me"
    )
    oidc_issuer = str(overrides.get("KBF_OIDC_ISSUER") or _env("KBF_OIDC_ISSUER") or "")
    oidc_client_id = str(
        overrides.get("KBF_OIDC_CLIENT_ID")
        or _env("KBF_OIDC_CLIENT_ID", "rbpfinder")
        or "rbpfinder"
    )
    oidc_client_secret = overrides.get("KBF_OIDC_CLIENT_SECRET")
    if oidc_client_secret is None:
        oidc_client_secret = _env("KBF_OIDC_CLIENT_SECRET")
    oidc_scopes = str(
        overrides.get("KBF_OIDC_SCOPES")
        or _env("KBF_OIDC_SCOPES", "openid profile email")
        or "openid profile email"
    )
    upstream_base_url = str(
        overrides.get("UPSTREAM_BASE_URL")
        or overrides.get("KBF_UPSTREAM_BASE_URL")
        or _env("KBF_UPSTREAM_BASE_URL", "http://127.0.0.1:18095")
        or "http://127.0.0.1:18095"
    ).rstrip("/")
    session_cookie_name = str(
        overrides.get("SESSION_COOKIE_NAME")
        or _env("KBF_SESSION_COOKIE_NAME", "rbpfinder_session")
        or "rbpfinder_session"
    )
    secure_cookies = str(
        overrides.get("SESSION_COOKIE_SECURE")
        or _env("KBF_SESSION_COOKIE_SECURE", "true")
        or "true"
    ).lower() not in {"0", "false", "no"}
    required_role = str(
        overrides.get("KBF_REQUIRE_ROLE")
        or _env("KBF_REQUIRE_ROLE", "")
        or ""
    ).strip() or None
    forward_auth_secret = str(
        overrides.get("KBF_FORWARD_AUTH_SECRET")
        or _env("KBF_FORWARD_AUTH_SECRET", "")
        or ""
    ).strip() or None

    return KbfSettings(
        testing=testing,
        secret_key=secret_key,
        oidc_issuer=oidc_issuer.rstrip("/"),
        oidc_client_id=oidc_client_id,
        oidc_client_secret=str(oidc_client_secret).strip() if oidc_client_secret else None,
        oidc_scopes=oidc_scopes,
        upstream_base_url=upstream_base_url,
        session_cookie_name=session_cookie_name,
        secure_cookies=secure_cookies,
        required_role=required_role,
        forward_auth_secret=forward_auth_secret,
    )
