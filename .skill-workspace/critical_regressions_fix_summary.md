# Critical-Regression Fix Pass — Summary & Verification Sign-off

Branch `fix/critical-regressions` (LOCAL ONLY). Re-ran the EXACT break tests that exposed each issue. All HOLD.

## Fixes
### CRIT-reg #1 — refresh-rotation TOCTOU race (was: 1 token → N live sessions)
- control/ai_mesh_control/auth/views.py: new AtomicTokenRefreshSerializer + AtomicTokenRefreshView. Takes a per-jti
  Postgres `pg_advisory_xact_lock(hashtext(jti))` inside the rotation transaction, so concurrent refreshes of the
  SAME token serialize — first rotates+blacklists, rest see the committed blacklist and get 401.
- control/ai_mesh_control/auth/urls.py: refresh route now uses AtomicTokenRefreshView.
- VERIFIED: 12 concurrent refreshes of one token → exactly 1×200, 11×401, 1 usable access (was 6/8). Serial
  rotation + old-token-401 + logout-revocation regression all green.

### CRIT-reg #2 — login throttle bypassable via spoofed X-Forwarded-For (was: 0/40 → 429)
- ROOT CAUSE was twofold: (a) app: DRF NUM_PROXIES unset; (b) server: daphne ran with `--proxy-headers`, which
  trusts client XFF and overwrites REMOTE_ADDR BEFORE DRF sees it (so NUM_PROXIES alone wasn't enough).
- main_app/settings.py: REST_FRAMEWORK['NUM_PROXIES'] = int(env NUM_PROXIES, default 0). 0=direct/local;
  set to trusted-proxy count behind nginx in prod.
- docker-compose.yml: control `command:` override drops `--proxy-headers` from daphne, so the app (via NUM_PROXIES)
  controls proxy trust instead of the server blindly trusting client XFF.
- VERIFIED: 16 logins, unique emails + rotating XFF (9.9.9.0..15) → [401×9, 429×7] — throttle fires at the 10/min
  per-IP limit despite spoofed XFF (was 0×429 across 40). (It even 429'd the test harness's own follow-up logins —
  proof it's live.)

### NEW high break — get_request_organization superuser fail-open (was: cross-tenant read/write/delete)
- control/ai_mesh_control/auth/utils.py: when a superuser passes an explicit organization_id that fails to parse
  OR doesn't resolve, RAISE ValidationError (400) instead of returning None (which let leaky superuser views fall
  through to an all-tenants queryset). No-org-id path unchanged (own org).
- VERIFIED: ?organization_id=abc / 0 / 99999 → 400 (was all-org aggregation + cross-org PATCH/DELETE); no-org →
  200 own-org.

## Deploy notes
- Verified on the local docker stack. Recreated the control container with the new daphne command, re-deployed all
  10 changed files (docker cp), restarted, migrated (token_blacklist already applied), check clean.
- PROD deploy: (1) rebuild control image OR keep the compose command override so daphne drops --proxy-headers;
  (2) set env NUM_PROXIES to the real trusted-proxy count (e.g. 1 behind nginx, 2 with nginx+Cloudflare) AND ensure
  nginx sets/normalizes X-Forwarded-For; (3) the advisory-lock rotation is Postgres-specific (guarded on
  connection.vendor=='postgresql').

## Remaining break-test items NOT in this pass (for follow-up)
MCP metadata-channel sanitize bypass (regression #3); gateway-key Redis eviction / no startup re-sync (regression
#4); /v1/policy/check 8KB-truncation bypass; vector-policy project_id key mismatch + version-regression; concurrent
policy PATCH 500 (policy_version_unique); unauth+uncapped ingestion data-loss; DEBUG=True info-leak; change-password
doesn't invalidate tokens; Postgres max_connections=100 no pooler.
