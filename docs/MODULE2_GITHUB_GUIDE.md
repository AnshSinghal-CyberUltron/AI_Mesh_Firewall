# Module 2 GitHub Guide

## Purpose

This guide explains how to work on Module 2 in this repository from a GitHub contribution and release perspective.

It is scoped to Module 2 and does not modify existing Module 1 documentation.

## Repository Map (Module 2 Relevant)

- Backend:
  - `control/ai_mesh_control/module2/`
  - `control/ai_mesh_control/module2/tests/`
- Frontend:
  - `frontend/src/pages/module2/`
  - `frontend/src/components/module2/`
  - `frontend/src/api/module2.js`
  - `frontend/src/api/killSwitch.js`
- Docs:
  - `docs/MODULE2_*`

## Branching and PR Strategy

- Use small, reviewable branches per concern:
  - example: `module2-docs-architecture`
  - example: `module2-incidents-hardening`
- Keep docs and code changes in the same PR only when docs explain that exact change.
- Prefer separate PRs for:
  - security hardening
  - UI behavior changes
  - operational/runbook changes
  - docs-only updates

## Recommended Commit Style

- Use clear scope in subject line:
  - `module2: add incident metadata allowlist docs`
  - `module2: tighten threat intel write authorization`
- In body, include:
  - why change is needed
  - user/ops impact
  - verification commands used

## Local Validation Commands

From repo root:

### Frontend Module 2 tests

```powershell
cd frontend
npm run test:module2
```

### Frontend production build

```powershell
cd frontend
npm run build
```

### Backend Module 2 tests (Postgres-backed)

```powershell
.\scripts\run-control-tests-postgres.ps1 module2.tests
```

### Single backend regression test

```powershell
.\scripts\run-control-tests-postgres.ps1 module2.tests.test_module2_pages_api.Module2PagesApiTests.test_threat_intel_write_requires_admin_or_superuser
```

## Module 2 PR Checklist

- [ ] Scope is limited to Module 2 paths or explicitly justified shared changes
- [ ] Frontend tests pass (`npm run test:module2`)
- [ ] Frontend build passes (`npm run build`)
- [ ] Backend Module 2 tests pass via Postgres runner
- [ ] API behavior validated for changed endpoints
- [ ] Security impact assessed (authz, data exposure, input validation)
- [ ] Docs updated in `docs/MODULE2_*` when behavior changes

## Release Gate References

- Readiness baseline:
  - [Module 2 Release Readiness Checklist](./MODULE2_RELEASE_READINESS_CHECKLIST.md)
- Architecture baseline:
  - [Module 2 Architecture](./MODULE2_ARCHITECTURE.md)
- Operational rollout:
  - [Module 2 Operations Runbook](./MODULE2_OPERATIONS_RUNBOOK.md)

## Issue and Review Labels (Recommended)

Use consistent labels for triage and release visibility:

- `module2`
- `module2-security`
- `module2-docs`
- `module2-ops`
- `release-blocker`
- `needs-manual-qa`

## What Not To Do

- Do not edit Module 1 docs when documenting Module 2.
- Do not merge Module 2 PRs without running Module 2-specific test commands.
- Do not ship endpoint contract changes without updating Module 2 technical docs.

## Related Module 2 Docs

- [Module 2 Docs Index](./MODULE2_DOCS_INDEX.md)
- [Module 2 Architecture](./MODULE2_ARCHITECTURE.md)
- [Module 2 Product Manual (Client)](./MODULE2_PRODUCT_MANUAL_CLIENT.md)
- [Module 2 Technical Manual (Developers)](./MODULE2_TECHNICAL_MANUAL_DEVELOPERS.md)
- [Module 2 Operations Runbook](./MODULE2_OPERATIONS_RUNBOOK.md)

