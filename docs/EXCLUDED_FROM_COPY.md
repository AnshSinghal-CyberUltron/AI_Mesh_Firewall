# Excluded from copy — rationale

Items intentionally **not** migrated into `AI_Mesh_Firewall/`. Parent monorepo retains them.

## Whole trees

| Path | Reason |
|------|--------|
| `backend/device/` | Endpoint device fleet — not AI Mesh Firewall |
| `backend/agent_distribution/` | MSI/PKG agent packaging |
| `backend/data_pipeline/` | Not in INSTALLED_APPS; separate ingestion |
| `backend/third_party_integrations/` | AIGuardX integrations (Copilot/GitHub export) |
| `endpoint_agent/`, `agent/` | Desktop/MITM agent product |
| `frontend/src/pages/aiguardx/` | Module 5 AIGuardX |
| `frontend/src/pages/behaviour_and_threat_intelligence/` | Module 2 |
| `frontend/src/pages/ai_infrastructure/` | Module 3 |
| `integrations/` | LangChain/Llama adapters for monorepo demos |

## Gateway files

| File | Reason |
|------|--------|
| `gateway/agent_proxy.py` | Proxies device enrollment to `/api/devices/*` |
| `gateway/_test_guardrails.py` | Dev script; imports non-product `mcp_gateway` |

## Control (`core/`) files

| File | Reason |
|------|--------|
| `agent_urls.py`, `agent_views.py` | Desktop agent register/download |
| *(kept)* `agent_auth.py` | Gateway/org API key auth for `/api/policy/check/` — not endpoint MSI |
| `pkg_builder.py`, `msi_builder.py`, `windows_agent_msi.py` | Installer build |
| `org_ca_views.py` | Org MSI signing CA |
| `endpoint_urls.py` | Wrapper for agent endpoints |
| `admin_views.py` (partial) | AIGuardX agent version stats — review before re-adding |
| `static/agents/*` | Binary installers |

## Frontend

| Path | Reason |
|------|--------|
| `pages/UserManagement.jsx` | Platform admin, not firewall SKU |
| `pages/Dashboard.jsx` | Cross-module home |
| `components/widgets/`, `components/agent/`, `components/charts/` | Unused or other modules |
| `ServiceStatusPanel.jsx` | Removed from overview per product decision (infra health) |

## Policy

| Item | Reason |
|------|--------|
| `policy/views.py` `by_device` action | Returns 410 — device fleet not shipped |
| `poc_views.py` AIGuardX questionnaire | Removed from standalone SKU |
| `/api/agents/register|telemetry|gateway-url/` | Replaced by `/api/gateways/instances/*` and `public-url/` |

## Tests & artifacts

| Path | Reason |
|------|--------|
| `tests/integration/test_device_*`, `test_agent_*` | Require excluded apps |
| `tests/security/reports/*.html` | Generated garak/promptfoo output |
| `runs/*.csv`, `ai_load_tests/results/` | Benchmark artifacts |
| `.playwright-mcp/` | Debug captures |

## Deprecated / trim later (copied but flagged)

| Item | Note |
|------|------|
| ChromaDB client in `vector_client.py` | Not in compose; use pinecone/milvus/pgvector |
| Frontend `chroma` default in vector panels | Change default to `pinecone` in follow-up PR |
| `main.py` Bedrock admin routes | Ops-only; can disable via env for slim deploy |
