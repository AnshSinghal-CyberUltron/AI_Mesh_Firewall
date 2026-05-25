# Code extraction plan (post 5-agent triage)

**Status:** Executed 2026-05-25 — `scripts/extract_from_monorepo.sh` + `scripts/post_extract_fixes.py`.

## Final decision (reconciled)

| Layer | Decision | Reasoning |
|-------|----------|-----------|
| **Gateway** | Copy full `gateway/` except `agent_proxy.py`, `_test_guardrails.py`; strip agent router from `main.py` | Inclusive data plane; agent path is AIGuardX fleet, not Module 1 |
| **Control** | Copy `auth`, `policy`, `core` (trimmed), `security_engines`, `ws`, `mcp_connector`, `main_app` (trimmed) | Modular monolith per plan; exclude `device`, `agent_distribution` |
| **Workers** | Copy task modules into `workers/ai_mesh_workers/` | Same Celery queues; beat/worker split preserved |
| **Frontend** | Copy firewall pages/components + shell deps; **exclude** `UserManagement`, all `aiguardx/*`, modules 2–5 | Firewall-only product UI |
| **Shared** | Merge monorepo `shared/` into `ai_mesh_shared/` | Job envelopes, normalizers, redis log handler |
| **Services stubs** | Keep scaffolds; **do not** copy duplicate logic into stubs yet | Phase 5 extraction only when SLO triggers |
| **Chroma** | Copy code but **document as deprecated**; UI defaults → pinecone; compose has no chromadb | Prod path is pgvector + external vector providers |
| **Tests** | Copy `tests/unit` firewall-related + `gateway/tests`; exclude device/agent/chroma-default integration | Slim CI |

## Excluded (documented in EXCLUDED_FROM_COPY.md)

- `backend/device/`, `backend/agent_distribution/`
- `endpoint_agent/`, `agent/`
- `gateway/agent_proxy.py`
- Frontend: `pages/aiguardx/*`, `behaviour_*`, `ai_infrastructure/*`, `UserManagement` (platform)
- `core/agent_*`, `pkg_builder`, `msi_builder`, `windows_agent_msi`, `org_ca_views` (desktop agent distribution)
- Root artifacts: `runs/`, `.playwright-mcp/`, `tests/security/reports/`, POC HTML at repo root

## Execution phases

1. Run `scripts/extract_from_monorepo.sh`
2. Post-process `main_app/settings.py`, `urls.py`, `asgi.py`, `routing.py`
3. Patch `gateway/main.py` (remove agent proxy, fix sys.path)
4. Bulk import renames `shared.` → `ai_mesh_shared.`
5. Frontend `App.jsx` firewall shell
6. Update Dockerfiles / pyproject from parent

## Verification

```bash
cd AI_Mesh_Firewall
docker compose build control gateway
docker compose up -d postgres redis rabbitmq control gateway
curl http://127.0.0.1:8100/api/health/
curl http://127.0.0.1:8300/health
```
