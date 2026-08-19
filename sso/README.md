# SSO forward_auth gateway

Keycloak OIDC forward_auth gateway that sits in front of the Bio Model Portal
(Caddy `forward_auth` → this app → Next.js frontend). It authenticates users
against the `kbf` realm and injects the authenticated identity downstream:

- `/auth/verify` returns `X-KBF-User` (OIDC sub), `X-KBF-Email`, `X-KBF-Name`,
  and `X-KBF-Auth` (shared secret). Caddy `copy_headers` forwards these to the
  backend, which trusts `X-KBF-User` only when `X-KBF-Auth` matches.
- `/login`, `/auth/callback`, `/logout` drive the OIDC + end-session flow.

## Run

```
python run.py            # reads HOST/PORT + KBF_* env (see .env.example)
pytest                   # tests/test_gateway_auth.py
```

## Deployment note

The live instance runs from `/opt/bio_model_portal_sso` (systemd unit
`bmp-sso`, port 18098) with its own `.env`. This directory is the
version-controlled source of truth — keep the two in sync when editing.
