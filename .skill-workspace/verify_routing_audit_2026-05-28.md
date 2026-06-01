# Phase 6E — Routing audit trail fix

## Fix: Routing audit trail & evidence pipeline

| Check | Status |
|-------|--------|
| Frontend `npm run build` | PASS |
| Backend unit test (local pytest) | BLOCKED — celery not in host Python; use `docker compose exec control python manage.py test ai_mesh_control.core.tests.test_routing_telemetry_metadata` when stack is up |
| Live Playwright firewall-1-5 | PENDING — requires local stack |
| Homepage 1.5 submodule chart | Code path fixed via ModuleKpisView/ModuleTrendsView |

## Changes shipped

1. **Backend:** Hoist `model_routed` fields in `control/ai_mesh_control/core/tasks.py` and `workers/ai_mesh_workers/tasks/telemetry.py`.
2. **Backend:** Module 1.5 KPIs/trends count `routing` + `model_routed` + `agentic_scan` / `AGENTIC`.
3. **Backend:** Threat feed OpenAPI `source` enum includes `routing`.
4. **Frontend:** `routingEventFields.js`, `MODULE_FILTERS` / `MODULE_SOURCE_MAP` for 1.5, Context column on evidence table.
5. **Frontend:** `footerPanels` — Routing Audit Trail below Recent routing evidence; shared threat feed (no duplicate fetch on 1.5 page).

## Manual verification

1. Open `http://localhost:8180/?tab=firewall-1-5`.
2. Confirm order: controls → simulator → **Recent routing evidence** → **Routing audit trail** (bottom).
3. Run Model Routing Simulator or gateway chat with routing enabled.
4. Within ~15s, evidence rows show Requested / Routed / Context; audit trail lists same decisions.
5. Open AI Mesh homepage — Multi-Model Governance card chart should show non-zero pressure when routing events exist.
