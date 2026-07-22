# Module 2 Operations Runbook

## Purpose

This runbook defines production-safe operating procedures for Module 2 deployment, rollback, and post-deploy validation.

Scope:

- Module 2 backend APIs and tasks
- Module 2 frontend pages/assets
- Module 2 smoke verification

## 1) Ownership and Escalation

- Primary owner: Module 2 engineering on-call
- Secondary owner: SOC platform operations
- Security escalation: security lead/on-call

Escalate immediately if:

- incident data exposure/authz issue is suspected
- incident action endpoints fail for active investigations
- threat intel writes become unauthorized or unavailable in production

## 2) Preconditions for Deployment

- release branch approved and merged
- Module 2 test gates green
- deployment window approved
- rollback target identified (previous image tag/commit)

Required checks:

- frontend build passed
- frontend module2 tests passed
- backend module2 tests passed (Postgres path)

## 3) Deployment Procedure (Containerized Stack)

From repository root:

### Step A: Pull latest code

```powershell
git pull
```

### Step B: Rebuild affected services

```powershell
docker compose up -d --build control frontend
```

If task behavior changed, also rebuild workers:

```powershell
docker compose up -d --build workers
```

### Step C: Confirm service health

```powershell
docker compose ps
```

Expected key services healthy/running:

- `control`
- `frontend`
- `workers`
- `postgres`
- `redis`
- `rabbitmq`

## 4) Post-Deploy Smoke Validation

Use an authenticated token and run these checks:

### Dashboard and incidents

- `GET /api/module2/dashboard/?period=24h` -> `200`
- `GET /api/module2/incidents/?queue=active` -> `200`
- invalid filter `severity=urgent` -> `400`

### Threat intel authz and mutation

- admin create threat intel -> `201`
- admin delete threat intel -> `204`
- non-admin create threat intel -> `403`

### Frontend sanity

- `/dashboard` renders KPI cards and timeline
- `/ueba/api-keys` loads summary/table and key detail panel
- `/incidents` filter and action controls render
- `/incidents/:id` shows timeline evidence and prompt JSON section when present

Reference release checks:

- [Module 2 Release Readiness Checklist](./MODULE2_RELEASE_READINESS_CHECKLIST.md)

## 5) Rollback Procedure

Trigger rollback when:

- smoke validation fails on critical endpoints
- user-visible regression blocks SOC workflow
- security control fails (authz/scope/redaction)

Rollback steps:

1. Redeploy previous known-good image/tag for impacted services.
2. Restart impacted service set.
3. Re-run smoke validation matrix.
4. Announce rollback and open incident ticket with root-cause owner.

Example (compose-based local/prod-like environment):

```powershell
docker compose up -d --build control frontend workers
```

Use previous commit/image context from deployment records before running this step.

## 6) Operational Failure Playbooks

### API 500 on Module 2 dashboard or incidents

- verify `control` container is running latest build
- inspect control logs for stack trace
- check db/migration compatibility and env values
- rollback if user-facing impact is ongoing

### Slow timeline/KPI response

- inspect endpoint timing logs (`elapsed_ms`)
- check database pressure and query plan changes
- check task backlog and worker liveness

### Incident action mutation fails

- verify security endpoints and auth token validity
- verify role permissions and org assignment
- check recent permission class or serializer changes

## 7) Observability and Monitoring Recommendations

Track these indicators continuously:

- Module 2 endpoint p95 latency (`/dashboard/`, `/incidents/`, `/incidents/{id}/`)
- 4xx/5xx rates by endpoint
- worker queue depth and task runtime for module2 tasks
- threat intel sync success/failure count
- incident mutation success/failure count

## 8) Release Sign-Off Record (Recommended Template)

- Release ID:
- Commit/tag:
- Operator:
- Start time:
- End time:
- Smoke results:
- Rollback needed (yes/no):
- Follow-up tickets:

## 9) Related Docs

- [Module 2 Docs Index](./MODULE2_DOCS_INDEX.md)
- [Module 2 Architecture](./MODULE2_ARCHITECTURE.md)
- [Module 2 Product Manual (Client)](./MODULE2_PRODUCT_MANUAL_CLIENT.md)
- [Module 2 Technical Manual (Developers)](./MODULE2_TECHNICAL_MANUAL_DEVELOPERS.md)
- [Module 2 GitHub Guide](./MODULE2_GITHUB_GUIDE.md)
- [Module 2 Release Readiness Checklist](./MODULE2_RELEASE_READINESS_CHECKLIST.md)

