# Auth & Session Hardening — Fix Summary & Verification Sign-off

Branch: `fix/auth-session-hardening` (LOCAL ONLY — no push/merge/PR per skill).
Scope: 4 findings (auth #1–#4). Verified live against the local docker stack (control container).

## Files changed (mine — 7 files; the rest of the working-tree diff is pre-existing, not authored here)
- main_app/settings.py — SIMPLE_JWT (60m/7d + ROTATE + BLACKLIST_AFTER_ROTATION); token_blacklist app;
  REST_FRAMEWORK DEFAULT_THROTTLE_RATES (login_ip 10/min, login_user 5/min; env-overridable).
- auth/views.py — LogoutView blacklists the refresh token; login view throttle_classes; docstring/example 5m→60m.
- auth/serializers.py — constant-time login (dummy check_password on unknown email).
- auth/throttling.py — NEW LoginIPThrottle + LoginEmailThrottle (Redis-backed scoped throttles).
- core/kill_switch_views.py — get_permissions(): mutating actions require IsAdminOrSuperuser.
- core/serializers.py — KillSwitchCreateSerializer rejects __global__ outright.
- core/model_state_views.py — ModelIsolateView/ModelRecoverView require IsAdminOrSuperuser; patch admin-gated;
  isolate rejects __global__.
- DB migration: rest_framework_simplejwt.token_blacklist (additive; applied to local dev DB).

## Mapping to findings
- auth #1 (30d TTL): ACCESS_TOKEN_LIFETIME=60m, REFRESH=7d. VERIFIED expires_in=3600.0, token exp-iat=3600s.
- auth #2 (no revocation): ROTATE_REFRESH_TOKENS + BLACKLIST_AFTER_ROTATION + LogoutView.blacklist(). VERIFIED
  old refresh replay→401, logout→204, refresh-after-logout→401.
- auth #3 (no throttle + enumeration): per-IP + per-email login throttle (429) + constant-time auth. VERIFIED
  429 fires in burst; timing gap 653ms vs 583ms = 70ms (<150ms; was ~400ms zero-overlap).
- auth #4 (kill-switch DoS): mutating kill-switch/isolate/recover = org-admin|superuser, own-org; __global__ rejected.
  VERIFIED non-admin create→403, non-admin isolate→403, admin __global__→400, admin specific-model→201.

## Verification sign-off (Phase 6E)
Services running locally (docker compose):   YES (control healthy)
manage.py check:                              PASS (only a pre-existing unrelated W342 warning)
token_blacklist migration applied:            YES
Live behavior checks:                         14/14 PASS
Existing auth profile tests:                  5/5 PASS (no regression)
Files compile / no circular imports:          YES
Status: VERIFIED

## Caveats / follow-ups
- Verification used `docker cp` into the RUNNING control container (ephemeral writable layer) + restart. The
  authoritative source change is on the host branch; deploying to prod requires REBUILDING the control image
  (per the docker-cp-ephemeral caveat) and running `manage.py migrate` (token_blacklist) there.
- LOGIN throttle is per-IP 10/min + per-email 5/min (env-tunable LOGIN_THROTTLE_IP_RATE / _USER_RATE). If many
  users sit behind one NAT/proxy, consider raising the IP rate or keying on a trusted forwarded-for.
- Frontend must send the `refresh` token in the /api/auth/logout/ body to get revocation (added as optional field).
- NOT in this scope (separate findings, deferred): change-password token revocation (auth #5), the broader
  cross-org organization_id isolation breach, MCP redaction/actor-scope, vector-policy key mismatch, perf/control-model.
