from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from contextlib import contextmanager
from dataclasses import dataclass
from http.cookies import SimpleCookie
from io import BytesIO
from typing import Iterator
from urllib.parse import parse_qs, quote, urlencode

import requests

from kbf_auth import (
    build_authorization_url,
    build_end_session_url,
    exchange_authorization_code,
    sanitize_next_path,
    validate_oidc_access_token,
)
from kbf_config import KbfSettings, load_settings


PUBLIC_PATHS = {
    "/auth/callback",
    "/auth/verify",
    "/healthz",
    "/login",
    "/logout",
    "/logout-bridge.html",
    "/logout/local",
}

HOP_BY_HOP_HEADERS = {
    "connection",
    "content-length",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def _sign_payload(secret_key: str, payload: str) -> str:
    digest = hmac.new(secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _encode_session(secret_key: str, session_data: dict) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps(session_data, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii")
    signature = _sign_payload(secret_key, payload)
    return f"{payload}.{signature}"


def _decode_session(secret_key: str, raw_value: str | None) -> dict:
    if not raw_value or "." not in raw_value:
        return {}
    payload, signature = raw_value.rsplit(".", 1)
    expected = _sign_payload(secret_key, payload)
    if not hmac.compare_digest(signature, expected):
        return {}
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        data = json.loads(decoded.decode("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


@dataclass
class RequestContext:
    method: str
    path: str
    query_string: str
    headers: dict[str, str]
    body: bytes
    session: dict
    session_modified: bool = False

    @property
    def full_path(self) -> str:
        return f"{self.path}?{self.query_string}" if self.query_string else self.path

    @property
    def args(self) -> dict[str, str]:
        parsed = parse_qs(self.query_string, keep_blank_values=True)
        return {key: values[-1] if values else "" for key, values in parsed.items()}

    @property
    def host(self) -> str:
        return self.headers.get("x-forwarded-host") or self.headers.get("host") or "localhost"

    @property
    def scheme(self) -> str:
        return self.headers.get("x-forwarded-proto") or "http"

    def external_url_for(self, path: str) -> str:
        return f"{self.scheme}://{self.host}{path}"

    def set_session(self, data: dict) -> None:
        self.session = data
        self.session_modified = True

    def clear_session(self) -> None:
        self.session = {}
        self.session_modified = True


class GatewayResponse:
    def __init__(self, body: bytes = b"", status_code: int = 200, headers: dict[str, str] | None = None):
        self.body = body
        self.status_code = status_code
        self.headers = headers or {}

    def get_data(self, as_text: bool = False):
        return self.body.decode("utf-8") if as_text else self.body


def _text_response(text: str, status_code: int = 200, content_type: str = "text/plain; charset=utf-8") -> GatewayResponse:
    return GatewayResponse(
        body=text.encode("utf-8"),
        status_code=status_code,
        headers={"Content-Type": content_type},
    )


def _json_response(payload: dict, status_code: int = 200) -> GatewayResponse:
    return GatewayResponse(
        body=json.dumps(payload).encode("utf-8"),
        status_code=status_code,
        headers={"Content-Type": "application/json"},
    )


def _redirect_response(location: str) -> GatewayResponse:
    return GatewayResponse(body=b"", status_code=302, headers={"Location": location})


def _build_logout_bridge_html(parent_origin: str, request_id: str) -> str:
    safe_parent_origin = parent_origin or "*"
    safe_request_id = request_id or ""
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>logout-bridge</title>
  </head>
  <body>logout-bridge:ok
    <script>
      (function () {{
        var parentOrigin = {safe_parent_origin!r};
        var requestId = {safe_request_id!r};
        try {{
          if (window.parent && window.parent !== window) {{
            window.parent.postMessage({{
              type: 'kbf:logout-bridge:complete',
              requestId: requestId,
              ok: true
            }}, parentOrigin || '*');
          }}
        }} catch (error) {{
          console.error('logout bridge failed', error);
        }}
        document.body.textContent = 'logout-bridge:ok';
      }})();
    </script>
  </body>
</html>
"""


def _identity_headers(kbf_user: dict) -> dict[str, str]:
    """Identity headers Caddy forward_auth copies onto the upstream request.

    Values are percent-encoded so non-ASCII names (e.g. Korean) stay valid in
    an HTTP header. The backend keys accounts on X-KBF-User (the OIDC sub).
    """
    headers: dict[str, str] = {}
    if not isinstance(kbf_user, dict):
        return headers
    for header_name, claim_key in (
        ("X-KBF-User", "sub"),
        ("X-KBF-Email", "email"),
        ("X-KBF-Name", "name"),
    ):
        value = str(kbf_user.get(claim_key) or "").strip()
        if value:
            headers[header_name] = quote(value, safe="@.")
    return headers


def _get_client_roles(claims: dict, client_id: str) -> set[str]:
    resource_access = claims.get("resource_access")
    if not isinstance(resource_access, dict):
        return set()
    client_block = resource_access.get(client_id)
    if not isinstance(client_block, dict):
        return set()
    roles = client_block.get("roles")
    if not isinstance(roles, list):
        return set()
    return {str(role).strip() for role in roles if str(role).strip()}


class KbfGatewayApp:
    def __init__(self, settings: KbfSettings):
        self.settings = settings

    def _load_session(self, headers: dict[str, str]) -> dict:
        cookie_header = headers.get("cookie") or ""
        cookie = SimpleCookie()
        cookie.load(cookie_header)
        morsel = cookie.get(self.settings.session_cookie_name)
        return _decode_session(self.settings.secret_key, morsel.value if morsel else None)

    def _build_set_cookie(self, session_data: dict) -> str:
        cookie = SimpleCookie()
        cookie[self.settings.session_cookie_name] = _encode_session(self.settings.secret_key, session_data)
        morsel = cookie[self.settings.session_cookie_name]
        morsel["path"] = "/"
        morsel["httponly"] = True
        morsel["samesite"] = "Lax"
        if self.settings.secure_cookies and not self.settings.testing:
            morsel["secure"] = True
        return morsel.OutputString()

    def _build_clear_cookie(self) -> str:
        cookie = SimpleCookie()
        cookie[self.settings.session_cookie_name] = ""
        morsel = cookie[self.settings.session_cookie_name]
        morsel["path"] = "/"
        morsel["expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
        morsel["max-age"] = "0"
        morsel["httponly"] = True
        morsel["samesite"] = "Lax"
        if self.settings.secure_cookies and not self.settings.testing:
            morsel["secure"] = True
        return morsel.OutputString()

    def _resolve_forward_auth_next_path(self, ctx: RequestContext) -> str:
        forwarded_uri = str(ctx.headers.get("x-forwarded-uri") or "").strip()
        next_path = sanitize_next_path(forwarded_uri or "/")
        return "/" if next_path == "/auth/verify" else next_path

    def _apply_session_cookie(self, response: GatewayResponse, ctx: RequestContext) -> GatewayResponse:
        if not ctx.session_modified:
            return response
        headers = dict(response.headers)
        if ctx.session:
            headers["Set-Cookie"] = self._build_set_cookie(ctx.session)
        else:
            headers["Set-Cookie"] = self._build_clear_cookie()
        response.headers = headers
        return response

    def _upstream_path(self, path: str) -> str:
        if path == "/api":
            return "/"
        if path.startswith("/api/"):
            stripped = path[len("/api") :]
            return stripped or "/"
        return path

    def _claims_missing_required_role(self, claims: dict) -> bool:
        if not self.settings.required_role:
            return False
        roles = _get_client_roles(claims, self.settings.oidc_client_id)
        return self.settings.required_role not in roles

    def _session_missing_required_role(self, ctx: RequestContext) -> bool:
        user = ctx.session.get("kbf_user")
        if not isinstance(user, dict):
            return False
        claims = user.get("claims")
        if not isinstance(claims, dict):
            return bool(self.settings.required_role)
        return self._claims_missing_required_role(claims)

    def _proxy_request(self, ctx: RequestContext) -> GatewayResponse:
        upstream_path = self._upstream_path(ctx.path)
        upstream_url = f"{self.settings.upstream_base_url}{upstream_path}"
        if ctx.query_string:
            upstream_url = f"{upstream_url}?{ctx.query_string}"

        upstream_headers = {}
        for key, value in ctx.headers.items():
            if key.lower() in {"host", "cookie"}:
                continue
            upstream_headers[key] = value
        upstream_headers["X-Forwarded-Host"] = ctx.host
        upstream_headers["X-Forwarded-Proto"] = ctx.scheme

        try:
            upstream = requests.request(
                ctx.method,
                upstream_url,
                headers=upstream_headers,
                data=ctx.body,
                allow_redirects=False,
                timeout=60,
            )
        except requests.RequestException:
            return _text_response("rbpfinder upstream unavailable", status_code=502)

        headers = {
            key: value
            for key, value in dict(upstream.headers).items()
            if key.lower() not in HOP_BY_HOP_HEADERS
        }
        return GatewayResponse(body=upstream.content, status_code=upstream.status_code, headers=headers)

    def handle_request(self, method: str, path: str, query_string: str = "", headers: dict[str, str] | None = None, body: bytes = b""):
        normalized_headers = {str(key).lower(): str(value) for key, value in (headers or {}).items()}
        ctx = RequestContext(
            method=method.upper(),
            path=path or "/",
            query_string=query_string,
            headers=normalized_headers,
            body=body,
            session=self._load_session(normalized_headers),
        )

        if ctx.path not in PUBLIC_PATHS and not ctx.session.get("kbf_user"):
            if ctx.method == "GET":
                next_path = sanitize_next_path(ctx.full_path)
                updated = dict(ctx.session)
                updated["after_login_path"] = next_path
                ctx.set_session(updated)
            return self._apply_session_cookie(_redirect_response("/login"), ctx)

        if ctx.path == "/healthz":
            return self._apply_session_cookie(_json_response({"status": "ok"}), ctx)

        if ctx.path == "/auth/verify":
            kbf_user = ctx.session.get("kbf_user")
            if kbf_user:
                if self._session_missing_required_role(ctx):
                    return self._apply_session_cookie(_text_response("forbidden", status_code=403), ctx)
                response = _text_response("ok")
                response.headers.update(_identity_headers(kbf_user))
                # Shared secret proving these identity headers came from the
                # gateway (Caddy copies it to the backend, which rejects the
                # identity unless it matches).
                if self.settings.forward_auth_secret:
                    response.headers["X-KBF-Auth"] = self.settings.forward_auth_secret
                return self._apply_session_cookie(response, ctx)
            next_path = self._resolve_forward_auth_next_path(ctx)
            login_location = f"/login?{urlencode({'next': next_path})}"
            return self._apply_session_cookie(_redirect_response(login_location), ctx)

        if ctx.path == "/login":
            if self.settings.testing:
                return self._apply_session_cookie(_text_response("login"), ctx)

            updated = dict(ctx.session)
            updated["oidc_state"] = secrets.token_urlsafe(24)
            updated["after_login_path"] = sanitize_next_path(
                ctx.args.get("next") or updated.get("after_login_path") or "/"
            )
            ctx.set_session(updated)
            redirect_uri = ctx.external_url_for("/auth/callback")
            auth_url = build_authorization_url(self.settings, redirect_uri, updated["oidc_state"])
            return self._apply_session_cookie(_redirect_response(auth_url), ctx)

        if ctx.path == "/auth/callback":
            if self.settings.testing:
                return self._apply_session_cookie(_text_response("invalid callback", status_code=400), ctx)

            expected_state = str(ctx.session.get("oidc_state") or "").strip()
            callback_state = str(ctx.args.get("state") or "").strip()
            code = str(ctx.args.get("code") or "").strip()
            if not expected_state or callback_state != expected_state or not code:
                return self._apply_session_cookie(_text_response("invalid callback", status_code=400), ctx)

            redirect_uri = ctx.external_url_for("/auth/callback")
            token_payload = exchange_authorization_code(code, redirect_uri, self.settings)
            access_token = str(token_payload.get("access_token") or "").strip()
            if not access_token:
                return self._apply_session_cookie(_text_response("missing access token", status_code=400), ctx)

            claims = validate_oidc_access_token(access_token, self.settings)
            if self._claims_missing_required_role(claims):
                ctx.clear_session()
                return self._apply_session_cookie(_text_response("forbidden", status_code=403), ctx)

            updated = dict(ctx.session)
            updated["kbf_user"] = {
                "sub": str(claims.get("sub") or ""),
                "email": str(claims.get("email") or ""),
                "name": str(claims.get("name") or claims.get("preferred_username") or ""),
                "claims": claims,
            }
            updated.pop("oidc_state", None)
            next_path = sanitize_next_path(updated.pop("after_login_path", "/"))
            ctx.set_session(updated)
            return self._apply_session_cookie(_redirect_response(next_path), ctx)

        if ctx.path == "/logout/local":
            ctx.clear_session()
            return self._apply_session_cookie(GatewayResponse(status_code=204), ctx)

        if ctx.path == "/logout":
            ctx.clear_session()
            if self.settings.testing:
                return self._apply_session_cookie(_redirect_response("/login"), ctx)

            login_url = ctx.external_url_for("/login")
            end_session_url = build_end_session_url(self.settings, login_url)
            return self._apply_session_cookie(_redirect_response(end_session_url or login_url), ctx)

        if ctx.path == "/logout-bridge.html":
            ctx.clear_session()
            response = _text_response(
                _build_logout_bridge_html(
                    parent_origin=str(ctx.args.get("parent_origin") or ""),
                    request_id=str(ctx.args.get("request_id") or ""),
                ),
                content_type="text/html; charset=utf-8",
            )
            return self._apply_session_cookie(response, ctx)

        if self._session_missing_required_role(ctx):
            return self._apply_session_cookie(_text_response("forbidden", status_code=403), ctx)

        return self._apply_session_cookie(self._proxy_request(ctx), ctx)

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO") or "/"
        query_string = environ.get("QUERY_STRING") or ""
        method = environ.get("REQUEST_METHOD") or "GET"
        body_length = int(environ.get("CONTENT_LENGTH") or "0" or 0)
        body = environ.get("wsgi.input", BytesIO()).read(body_length) if body_length else b""

        headers: dict[str, str] = {}
        for key, value in environ.items():
            if not isinstance(value, str):
                continue
            if key.startswith("HTTP_"):
                header_name = key[5:].replace("_", "-").lower()
                headers[header_name] = value
        if "CONTENT_TYPE" in environ and environ["CONTENT_TYPE"]:
            headers["content-type"] = environ["CONTENT_TYPE"]
        if "CONTENT_LENGTH" in environ and environ["CONTENT_LENGTH"]:
            headers["content-length"] = environ["CONTENT_LENGTH"]
        if "SERVER_NAME" in environ and "host" not in headers:
            host = environ["SERVER_NAME"]
            port = environ.get("SERVER_PORT")
            if port and port not in {"80", "443"}:
                host = f"{host}:{port}"
            headers["host"] = host

        response = self.handle_request(method, path, query_string=query_string, headers=headers, body=body)
        status_line = f"{response.status_code} {self._status_reason(response.status_code)}"
        start_response(status_line, list(response.headers.items()))
        return [response.body]

    @staticmethod
    def _status_reason(status_code: int) -> str:
        mapping = {
            200: "OK",
            204: "No Content",
            302: "Found",
            400: "Bad Request",
            403: "Forbidden",
            502: "Bad Gateway",
        }
        return mapping.get(status_code, "OK")

    def test_client(self):
        return GatewayTestClient(self)


class SessionTransaction:
    def __init__(self, client: "GatewayTestClient"):
        self.client = client
        self.data: dict = {}

    def __enter__(self):
        self.data = self.client._load_session()
        return self.data

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.client._store_session(self.data)
        return False


class GatewayTestClient:
    def __init__(self, app: KbfGatewayApp):
        self.app = app
        self.cookies: dict[str, str] = {}

    def _cookie_header(self) -> str:
        return "; ".join(f"{key}={value}" for key, value in self.cookies.items())

    def _apply_set_cookie(self, set_cookie: str | None) -> None:
        if not set_cookie:
            return
        cookie = SimpleCookie()
        cookie.load(set_cookie)
        morsel = cookie.get(self.app.settings.session_cookie_name)
        if morsel is None:
            return
        if morsel.value:
            self.cookies[self.app.settings.session_cookie_name] = morsel.value
        else:
            self.cookies.pop(self.app.settings.session_cookie_name, None)

    def _load_session(self) -> dict:
        raw = self.cookies.get(self.app.settings.session_cookie_name)
        return _decode_session(self.app.settings.secret_key, raw)

    def _store_session(self, session_data: dict) -> None:
        if session_data:
            self.cookies[self.app.settings.session_cookie_name] = _encode_session(
                self.app.settings.secret_key, session_data
            )
        else:
            self.cookies.pop(self.app.settings.session_cookie_name, None)

    @contextmanager
    def session_transaction(self) -> Iterator[dict]:
        transaction = SessionTransaction(self)
        try:
            yield transaction.__enter__()
        finally:
            transaction.__exit__(None, None, None)

    def _default_host(self) -> str:
        if self.app.settings.oidc_client_id == "rbpfinder-dev":
            return "dev-rbpfinder.k-biofoundrycopilot.duckdns.org"
        return "rbpfinder.k-biofoundrycopilot.duckdns.org"

    def open(self, path: str, method: str = "GET", follow_redirects: bool = False, data: bytes = b""):
        if "?" in path:
            request_path, query_string = path.split("?", 1)
        else:
            request_path, query_string = path, ""
        headers = {"Host": self._default_host()}
        if self.cookies:
            headers["Cookie"] = self._cookie_header()

        response = self.app.handle_request(
            method,
            request_path or "/",
            query_string=query_string,
            headers=headers,
            body=data,
        )
        self._apply_set_cookie(response.headers.get("Set-Cookie"))
        if follow_redirects and response.status_code in {301, 302, 303, 307, 308}:
            return self.open(response.headers.get("Location", "/"), method="GET", follow_redirects=True)
        return response

    def get(self, path: str, follow_redirects: bool = False):
        return self.open(path, method="GET", follow_redirects=follow_redirects)


def create_app(overrides: dict | None = None) -> KbfGatewayApp:
    return KbfGatewayApp(load_settings(overrides))


app = create_app()
