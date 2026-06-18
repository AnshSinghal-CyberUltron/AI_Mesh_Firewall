# Module 2 Release Readiness Checklist

Date: 2026-06-18

## Automated Verification

- [x] Core services healthy in `docker compose ps` (`control`, `postgres`, `redis`, `rabbitmq`, `gateway`, `frontend`)
- [x] Frontend production build succeeds (`frontend`: `npm run build`)
- [x] Frontend Module 2 test suite passes (`frontend`: `npm run test:module2`)
- [x] Backend Module 2 test suite passes on Postgres (`.\scripts\run-control-tests-postgres.ps1 module2.tests`)
- [x] API smoke: `GET /api/module2/dashboard/?period=24h` returns 200
- [x] API smoke: `GET /api/module2/incidents/?queue=active` returns 200
- [x] API smoke: invalid incident filter (`severity=urgent`) returns 400
- [x] API smoke: threat intel list returns 200
- [x] API smoke: threat intel create/delete as admin returns 201/204
- [x] API smoke: threat intel create as non-admin returns 403

## Security/Hardening Checks Implemented

- [x] Incident detail org scoping fixed for non-superuser org-less access
- [x] Incident detail metadata redaction allowlist enabled
- [x] Threat intel write operations gated to admin/superuser
- [x] Frontend Module 2 cache isolation by auth scope implemented
- [x] Kill-switch create/activate rollback compensation implemented
- [x] Alert/anomaly dedupe guards and transactional locking added
- [x] Incident filter validation now fail-fast on invalid enum values
- [x] Module 2 endpoint/task timing logs added

## Manual QA To Run Before Release

- [ ] Dashboard period switch stress test (rapid 1h/24h/7d flips)
- [ ] Incident queue mutate path UX (escalate/resolve under filters)
- [ ] Incident detail prompt JSON + copy action for chat lane evidence
- [ ] Threat intel workflow UX (create/edit/delete + sync behavior visibility)
- [ ] UEBA containment path (disable key, activate/deactivate kill switch)
- [ ] Realtime fallback behavior (WS connected/disconnected states)
- [ ] Accessibility pass for keyboard-only KPI help/tooltips and dialogs

