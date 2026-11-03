# GW36 S3 evidence — gateway state runtime, hashed identity, 401/503 split (3 November 2026)

Plan: `docs/plans/2026-11-03-gw36-s3-state-runtime-identity.md`. Live run on the shared dev VM
(user `suraj`, rootless Docker, stack ports on `127.0.0.1` +20000): a second gateway container
with `AMF_PROFILE=production` (2 uvicorn workers) reading the stack's real signed state, which the
S2 projector and re-hydrators write from the console's own tables.

| File | What it shows |
|---|---|
| `00_production_boot_log.txt` | The production boot names what is real (state, identity, plans, live state metrics) and what is still the fail-closed default (provider, admission, quota, detectors, catalog; kill switch not enforced) |
| `01_live_e1_e7.txt` | 17/17 live checks, official `openai` SDK for the 401s |
| `02_suite_and_gates.txt` | Offline suite and every repo gate |
| `live_s3.py` | The live check itself (console API, SDK, docker stop/start of this stack's own Valkey and re-hydrators) |

Headline numbers (live):

| Check | Result |
|---|---|
| E1 console key → resolves | `/v1/models` 200 after about 0.4–1 s; chat refused only at admission (`overload_shed`, S4), with id `zs-zeroshield-…` |
| E2 unknown / junk / missing key | `AuthenticationError` 401 `invalid_api_key`, streaming and not; `www-authenticate: Bearer`; random `zs-` id |
| E3 console deactivation | 401 on every worker 0.86–0.92 s after the PATCH |
| E4 Valkey down | 503 `shared_state_unavailable`, never 401; `/readyz` 503 past the 5 s bound; `/livez` 200 |
| E5 Valkey back | Valid key 200 again on every worker 0.7 s after start |
| E6 Re-hydrators stopped | 503 at 4.7 s (`FRESH_MS` 5 s); back in 0.7 s |
| E7 `/metrics` | 200 throughout; `amf_rejected{status="401",reason="rejected"}` counts the 401s |
