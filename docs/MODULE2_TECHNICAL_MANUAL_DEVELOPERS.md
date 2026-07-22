# Module 2 Technical Manual (Developers)

## 1) Scope

This manual covers implementation and maintenance of Module 2.

Primary code locations:

- Backend: `control/ai_mesh_control/module2/`
- Frontend pages: `frontend/src/pages/module2/`
- Frontend API client: `frontend/src/api/module2.js`

Module 2 is implemented independently, but uses shared models and infrastructure from the broader system.

## 2) Frontend Routing Map

Defined in `frontend/src/App.jsx`:

- `/dashboard` -> `DashboardPage`
- `/ueba/api-keys` -> `UebaApiKeysPage`
- `/models/exposure` -> `ModelExposurePage`
- `/mcp/risk` -> `McpRiskPage`
- `/threat-intel` -> `ThreatIntelPage`
- `/incidents` -> `IncidentsPage`
- `/incidents/:id` -> `IncidentDetailPage`

## 3) Backend Endpoint Map

Defined in `control/ai_mesh_control/module2/urls.py`:

- `GET /api/module2/dashboard/`
- `GET /api/module2/ueba/api-keys/summary/`
- `GET /api/module2/ueba/api-keys/timeline/`
- `GET /api/module2/ueba/api-keys/registry/`
- `GET /api/module2/ueba/api-keys/{uuid}/behavior/`
- `GET /api/module2/models/exposure/`
- `GET /api/module2/rag/health/`
- `GET /api/module2/mcp/risk/`
- `GET /api/module2/threat-intel/telemetry/`
- `POST /api/module2/threat-intel/sync/`
- `CRUD /api/module2/threat-intel/` (router viewset)
- `GET /api/module2/incidents/`
- `GET /api/module2/incidents/{id}/`

## 4) Frontend-to-Backend Contract Mapping

| Frontend page | Backend endpoint(s) | Notes |
|---|---|---|
| Dashboard | `/dashboard/` | Includes lane summary, threat trend, KPIs |
| UEBA API keys | `/ueba/api-keys/summary/`, `/timeline/`, `/registry/`, `/behavior/` | Multi-call page with per-key drill-down |
| Model/RAG | `/models/exposure/`, `/rag/health/` | Tabbed view behavior |
| MCP Risk | `/mcp/risk/` | Tool/server risk summary |
| Threat Intel | `/threat-intel/`, `/threat-intel/telemetry/`, `/threat-intel/sync/` | CRUD + telemetry + sync |
| Incident queue | `/incidents/` + security mutation APIs | Escalate/resolve call security endpoints |
| Incident detail | `/incidents/{id}/` + security mutation APIs | Includes timeline evidence rendering |

Mutation APIs used from frontend:

- `POST /api/security/incidents/{id}/escalate-incident/`
- `POST /api/security/incidents/{id}/resolve-incident/`

## 5) Data and Analytics Layer

Core aggregation behavior is in `control/ai_mesh_control/module2/views.py` and `analytics.py`.

Important patterns:

- org scoping via request org resolver
- bounded list/detail payloads for UI consumption
- event trend bucketing helper for timeline charts
- UEBA risk payload generation (`_risk_payload`)
- incident queue summary with source split and pagination

## 6) Security Controls Implemented

Current key controls:

- incident detail access guard for org-less non-superusers
- incident metadata allowlist/sanitization in detail timeline
- threat intel writes restricted to admin/superuser
- strict validation for incident filters (`status`, `severity`, `source`, `queue`)
- frontend module cache scoped by auth context
- kill-switch create/activate compensation rollback
- dedupe + transactional locking in alert/anomaly incident generation tasks

## 7) Reliability and Performance Controls

- request sequencing in dashboard and key pages to avoid stale async overwrite
- cache invalidation hooks after mutations
- reduced query amplification in timeline/trend generation
- endpoint and task timing logs for operational visibility

## 8) Testing Strategy

### Frontend

```powershell
cd frontend
npm run test:module2
npm run build
```

### Backend (Postgres-backed)

```powershell
.\scripts\run-control-tests-postgres.ps1 module2.tests
```

Single regression:

```powershell
.\scripts\run-control-tests-postgres.ps1 module2.tests.test_module2_pages_api.Module2PagesApiTests.test_threat_intel_write_requires_admin_or_superuser
```

## 9) Common Troubleshooting

### Symptom: incidents filter accepts invalid values

- Check running container build vs local code.
- Validate `IncidentListView` filter validation path.
- Rebuild/restart `control` if behavior differs from tests.

### Symptom: stale dashboard after rapid period change

- Verify request sequence guard in `DashboardPage`.
- Confirm no custom caller bypasses standard page load path.

### Symptom: threat intel write unexpectedly allowed

- Verify user role (staff/superuser/role mapping).
- Confirm `ThreatIntelViewSet` permission class includes admin-write gate.

### Symptom: backend tests fail locally with sqlite extension errors

- Use Postgres-backed script:
  - `.\scripts\run-control-tests-postgres.ps1 module2.tests`

## 10) Developer Change Checklist

- [ ] Update backend endpoint and serializer behavior
- [ ] Update frontend API client mapping if contract changed
- [ ] Add/adjust frontend module2 tests
- [ ] Add/adjust backend module2 tests
- [ ] Re-run build and module2 suites
- [ ] Update Module 2 docs (`docs/MODULE2_*`) for behavior or contract changes

## 11) Related Docs

- [Module 2 Docs Index](./MODULE2_DOCS_INDEX.md)
- [Module 2 Architecture](./MODULE2_ARCHITECTURE.md)
- [Module 2 GitHub Guide](./MODULE2_GITHUB_GUIDE.md)
- [Module 2 Product Manual (Client)](./MODULE2_PRODUCT_MANUAL_CLIENT.md)
- [Module 2 Operations Runbook](./MODULE2_OPERATIONS_RUNBOOK.md)
- [Module 2 Release Readiness Checklist](./MODULE2_RELEASE_READINESS_CHECKLIST.md)

