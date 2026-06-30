# Module 2 Release Readiness Checklist

Date: 2026-06-30 (re-verified after E2E fixes)

## Automated Verification

- [x] Core services healthy in `docker compose ps` (`control`, `postgres`, `redis`, `rabbitmq`, `gateway`, `frontend`)
- [x] Fresh DB: `docker compose down -v` + `up --build -d` applies migrations via control entrypoint (no manual migrate)
- [x] Frontend production build succeeds (`frontend`: `npm run build`)
- [x] Frontend unit tests pass (`frontend`: `npm run test:unit` — 26 tests)
- [x] Backend Module 2 test suite passes on Postgres (`docker compose exec control python manage.py test module2.tests` — 60 tests)
- [x] E2E smoke script (`.\scripts\e2e-release-smoke.ps1` — 12/12)
- [x] API smoke: `GET /api/module2/dashboard/?period=24h` returns 200
- [x] API smoke: `GET /api/module2/incidents/?queue=active` returns 200
- [x] API smoke: invalid incident filter (`severity=urgent`) returns 400
- [x] API smoke: threat intel telemetry returns 200
- [x] Gateway attack simulation blocks prompt injection (HTTP 400 content filter)
- [x] Admin bootstrap on cold start (`ensure_zeroshield_admin` via entrypoint)

## Security/Hardening Checks Implemented

- [x] Incident detail org scoping fixed for non-superuser org-less access
- [x] Incident detail metadata redaction allowlist enabled
- [x] Threat intel write operations gated to admin/superuser
- [x] Frontend Module 2 cache isolation by auth scope implemented
- [x] Kill-switch create/activate rollback compensation implemented
- [x] Alert/anomaly dedupe guards and transactional locking added
- [x] Incident filter validation now fail-fast on invalid enum values
- [x] Module 2 endpoint/task timing logs added
- [x] Control entrypoint: migrate retry + admin bootstrap on gunicorn/daphne start
- [x] Workers wait for control healthy before starting
- [x] Prod overlay: `SIMULATOR_DEFAULTS_ENABLED` defaults to `false`

## Manual QA To Run Before Release

- [ ] Dashboard period switch stress test (rapid 1h/24h/7d flips)
- [ ] Incident queue mutate path UX (escalate/resolve under filters)
- [ ] Incident detail prompt JSON + copy action for chat lane evidence
- [ ] Threat intel workflow UX (create/edit/delete + sync behavior visibility)
- [ ] UEBA containment path (disable key, activate/deactivate kill switch)
- [ ] Realtime fallback behavior (WS connected/disconnected states)
- [ ] Accessibility pass for keyboard-only KPI help/tooltips and dialogs

See also: [MODULE2_PRODUCTION_DEPLOY.md](./MODULE2_PRODUCTION_DEPLOY.md)
