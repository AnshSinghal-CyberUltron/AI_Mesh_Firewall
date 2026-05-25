# AI Mesh Firewall (Standalone Product)

ZeroShield **Module 1** as an independent product: L7 AI traffic governance (ingress, RAG, vector, MCP, routing, kill-switch, output guardrails).

This repository is **not** AIGuardX, endpoint agents, or behavioral analytics modules. Those live in the parent `AI_Security` monorepo until migrated.

## Architecture (hybrid — post triage)

| Tier | Component | Runtime | Why separated |
|------|-----------|---------|---------------|
| **Data plane** | `gateway/` | FastAPI | Hot path; scales on connections + RPS |
| **Control plane** | `control/` | Django (modular monolith) | Policy/tenant/key authoring; slower release cadence |
| **Async plane** | `workers/` | Celery | Telemetry drain, Tier-2, vector index, MCP audit |
| **UI** | `frontend/` | Vite + React | Operator console only |
| **Extractable services** | `services/*` | FastAPI workers | Guardrails, MCP, vector, telemetry — scale independently at 100k+ |
| **Contracts** | `shared/` + `docs/contracts/` | Python + JSON Schema | Stable cross-service APIs |

## Submodule map (1.1 – 1.7)

| Module | Primary owner |
|--------|----------------|
| 1.1 AI Gateway & ingress | `gateway/` |
| 1.2 RAG firewall | `gateway/rag_pipeline/` |
| 1.3 Vector DB firewall | `services/vector-retrieval/` + `gateway/` |
| 1.4 MCP guardrails | `services/mcp-broker/` + `gateway/` |
| 1.5 Multi-model routing | `gateway/` + `control/policy/` |
| 1.6 Kill-switch | `control/core/` + `gateway/` |
| 1.7 Output guardrails | `services/guardrails/` + `gateway/` |

## Quick start (local)

```bash
cp .env.sample .env
docker compose up -d postgres redis rabbitmq
docker compose up -d control workers gateway frontend
```

| Service | URL |
|---------|-----|
| UI | http://127.0.0.1:8180 |
| Control API | http://127.0.0.1:8100 |
| Gateway (direct) | http://127.0.0.1:8300 |
| RabbitMQ UI | http://127.0.0.1:15772 |

**Login (dev):** `admin@zeroshield.io` / `Adm1n!Pass#2024`

**Gateway from the browser:** simulators call `http://127.0.0.1:8180` (same origin); Vite proxies `/v1` and `/health` to the gateway container. If you still see `Cannot reach gateway at http://control:8000`, hard-refresh and run `localStorage.removeItem('zeroshield_gateway_url')` in the browser console, then restart: `docker compose up -d control gateway frontend`.

**Verify UI (9 modules, functional clicks):** `node scripts/playwright_nine_modules.mjs`

**Bedrock / simulator key (dev):** after `docker compose up`, run `bash scripts/seed_simulator_gateway_key.sh`. Ensure `AI_Mesh_Firewall/.env` or parent `../.env` has `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `BEDROCK_REGION`, `BEDROCK_MODEL`.

## Docs

- [Architecture](docs/ARCHITECTURE.md)
- [Repository layout](docs/REPOSITORY_LAYOUT.md)
- [Migration from AIGuardX monorepo](docs/MIGRATION_FROM_AIGUARDX.md)
- [100k concurrency deployment](docs/DEPLOYMENT_100K.md)
- [Event/API contracts](docs/contracts/)

## Migration source

Code is **extracted** from `../backend`, `../gateway`, `../frontend`, `../shared` in the parent repo. See `docs/MIGRATION_FROM_AIGUARDX.md` for file-level mapping and extraction phases.
