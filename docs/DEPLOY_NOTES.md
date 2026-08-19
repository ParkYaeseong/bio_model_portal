# Bio Model Portal — KBF Deployment Notes (2026-06-15)

Deployed on host `192.168.0.12` (Caddy/kbf-infra host). GPU workers are on the
remote host `211.188.35.221` (protein_pipeline GPU HTTP fleet, shared with RAPID).

## Public URL
- https://biomodel.k-biofoundrycopilot.duckdns.org  (KBF SSO gated)
- Also linked from the main portal (open-notebook) **Design** section.

## Services (systemd, enabled at boot)
| unit | port | role |
|---|---|---|
| bmp-gateway | 18122 | RunPod-v2-compatible gateway → GPU HTTP workers |
| bmp-backend | 18121 | FastAPI portal backend (SQLite, JWT) |
| bmp-frontend | 18120 | Next.js portal frontend (`next start`) |
| bmp-sso | 18098 | Keycloak forward_auth gateway (clone of RBPFinder/gateway) |

Source: `/opt/bio_model_portal` (branch `kbf-deploy`); SSO gateway: `/opt/bio_model_portal_sso`.

## Model routing (gateway/endpoints.yaml)

**Local GPU HTTP** (worker_url → `211.188.35.221`): proteinmpnn 18101,
rosetta-relax 18102, bioemu 18103, rfdiffusion 18104, diffdock 18105,
mmseqs 18107(GPU), colabfold 18160(LB), esmfold 18162. Smoke: `docs/SMOKE_RESULTS.md` (8/8 OK).

**RunPod serverless passthrough** (runpod_endpoint_id): alphafold-local →
`n3tcpxdv3irr46`, phastest-local → `hmhrwi5mvm8idm`. Gateway submits to
RunPod /run and polls /status to completion, normalizing output to the same
shape as a local worker (packaging unchanged). `RUNPOD_API_KEY` (reused from
protein_pipeline) + `RUNPOD_API_BASE` in `gateway/.env`. Both endpoints
health-verified (worker_status 200, ready workers). NOTE: AF2 input-schema
compatibility with `n3tcpxdv3irr46` should be confirmed with one real run.

## SSO
- Keycloak realm `kbf`, **public** client `bio-model-portal`
  (redirectUris `https://biomodel.k-biofoundrycopilot.duckdns.org/*`).
- Caddy block routes `/auth/* /login /logout /healthz` → SSO gateway (18098);
  everything else → `forward_auth /auth/verify` then frontend (18120).
- `/api` is intentionally NOT routed to the SSO gateway — it is the portal's own
  API, proxied by Next.js to the backend.

## Portal auth model (proxy-gate-only)
- The whole app is gated by KBF SSO at Caddy. Inside the portal, a SINGLE shared
  account (`kbfportal`) is used; the frontend auto-logs-in via the server-side
  `/bootstrap-login` route handler (credentials in server-only env
  `BOOTSTRAP_LOGIN_*` in `portal/frontend/.env.production`, never in the client
  bundle). Consequence: all SSO users share one portal identity / job list.
  Per-user isolation would require full OIDC integration (deferred).

## Caddy single-file bind-mount gotcha
`/opt/kbf-infra/Caddyfile` is a single-file RO bind-mount; editing it changes the
inode and the running container keeps the old one. Apply with:
`docker cp Caddyfile kbf-infra-caddy-1:/tmp/Caddyfile.new && docker exec ... caddy
reload --config /tmp/Caddyfile.new --adapter caddyfile`, then `docker restart
kbf-infra-caddy-1` to re-sync the mount. (Done; mount re-synced.)

## Open-notebook Design card
`api/routers/portal.py` `_biomodel_service()` (category "Design"), appended in
`_build_services_for_ctx`. Host source edited + hot-copied into the running
container + api process restarted. NOTE: the host edit is currently uncommitted
in the open-notebook repo working tree (build context includes it on rebuild);
fold into the open-notebook git/deploy workflow as appropriate.

## Verified (2026-06-15)
- External unauth → 302 to Keycloak authorize (client_id=bio-model-portal). ✅
- All 4 systemd services active + enabled. ✅
- 8/8 model endpoints proxy healthy + smoke-tested through adapter/packager. ✅
- Shared-account token via /bootstrap-login → authenticated /api/pipelines + /api/jobs
  through the full frontend→backend stack = 200. ✅
- No regression on rapid / rbpfinder / notebook / open-notebook root. ✅

## Needs a real KBF login to confirm (not automatable without credentials)
- Browser: Keycloak login → portal auto-login landing (no second login form).
- Browser: run a model from the UI and see artifacts.
- Browser: Design card visible on the main portal for an OIDC user.
