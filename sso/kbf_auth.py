from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlencode, urlparse

import requests

from kbf_config import KbfSettings


def normalize_issuer(issuer: str) -> str:
    normalized = issuer.strip().rstrip("/")
    if normalized.endswith("/.well-known/openid-configuration"):
        normalized = normalized[: -len("/.well-known/openid-configuration")]
    return normalized.rstrip("/")


@lru_cache(maxsize=16)
def fetch_oidc_discovery(settings: KbfSettings) -> dict:
    discovery_url = f"{normalize_issuer(settings.oidc_issuer)}/.well-known/openid-configuration"
    response = requests.get(discovery_url, timeout=10)
    response.raise_for_status()
    return response.json()


@lru_cache(maxsize=16)
def fetch_oidc_jwks(settings: KbfSettings) -> dict:
    discovery = fetch_oidc_discovery(settings)
    jwks_uri = str(discovery.get("jwks_uri") or "").strip()
    if not jwks_uri:
        raise RuntimeError("OIDC discovery did not return jwks_uri")
    response = requests.get(jwks_uri, timeout=10)
    response.raise_for_status()
    return response.json()


def sanitize_next_path(raw_path: str | None) -> str:
    candidate = str(raw_path or "").strip()
    if not candidate:
        return "/"

    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc:
        return "/"

    if not candidate.startswith("/"):
        candidate = f"/{candidate}"
    return candidate


def build_authorization_url(settings: KbfSettings, redirect_uri: str, state: str) -> str:
    discovery = fetch_oidc_discovery(settings)
    authorization_endpoint = str(discovery.get("authorization_endpoint") or "").strip()
    if not authorization_endpoint:
        raise RuntimeError("OIDC discovery missing authorization_endpoint")

    return f"{authorization_endpoint}?{urlencode({
        'response_type': 'code',
        'client_id': settings.oidc_client_id,
        'redirect_uri': redirect_uri,
        'scope': settings.oidc_scopes,
        'state': state,
    })}"


def exchange_authorization_code(code: str, redirect_uri: str, settings: KbfSettings) -> dict:
    discovery = fetch_oidc_discovery(settings)
    token_endpoint = str(discovery.get("token_endpoint") or "").strip()
    if not token_endpoint:
        raise RuntimeError("OIDC discovery missing token_endpoint")

    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": settings.oidc_client_id,
    }
    if settings.oidc_client_secret:
        payload["client_secret"] = settings.oidc_client_secret

    response = requests.post(token_endpoint, data=payload, timeout=10)
    response.raise_for_status()
    return response.json()


def _extract_audiences(claims: dict) -> set[str]:
    raw = claims.get("aud")
    if isinstance(raw, str):
        return {raw.strip()} if raw.strip() else set()
    if isinstance(raw, list):
        return {str(value).strip() for value in raw if str(value).strip()}
    return set()


def _claims_match_client(claims: dict, client_id: str) -> bool:
    audiences = _extract_audiences(claims)
    if client_id in audiences:
        return True
    azp = claims.get("azp")
    return isinstance(azp, str) and azp.strip() == client_id


def validate_oidc_access_token(access_token: str, settings: KbfSettings) -> dict:
    from jose import jwt

    header = jwt.get_unverified_header(access_token)
    kid = header.get("kid")

    jwks = fetch_oidc_jwks(settings)
    keys = jwks.get("keys") or []
    signing_key = None
    for key in keys:
        if not isinstance(key, dict):
            continue
        if kid and key.get("kid") == kid:
            signing_key = key
            break
    if signing_key is None and len(keys) == 1 and isinstance(keys[0], dict):
        signing_key = keys[0]
    if signing_key is None:
        raise RuntimeError("Unable to select JWKS signing key")

    claims = jwt.decode(
        access_token,
        signing_key,
        algorithms=["RS256"],
        options={
            "verify_aud": False,
            "verify_iss": False,
            "verify_at_hash": False,
        },
    )

    token_issuer = str(claims.get("iss") or "").strip()
    if normalize_issuer(token_issuer) != normalize_issuer(settings.oidc_issuer):
        raise RuntimeError("Invalid token issuer")
    if not _claims_match_client(claims, settings.oidc_client_id):
        raise RuntimeError("Invalid token audience")
    return claims


def build_end_session_url(settings: KbfSettings, post_logout_redirect_uri: str) -> str | None:
    discovery = fetch_oidc_discovery(settings)
    end_session_endpoint = str(discovery.get("end_session_endpoint") or "").strip()
    if not end_session_endpoint:
        return None
    return f"{end_session_endpoint}?{urlencode({
        'client_id': settings.oidc_client_id,
        'post_logout_redirect_uri': post_logout_redirect_uri,
    })}"
