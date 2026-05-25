# Migration from AIGuardX monorepo

Phased extraction from `../` (AI_Security) into this standalone product.

## Phase 0 — Scaffold (current)

- [x] Product directory tree + contracts + compose skeleton
- [ ] Wire CI for this subtree only

## Phase 1 — Control plane (`control/`)

| Source (monorepo) | Destination |
|-------------------|-------------|
| `backend/main_app/` | `control/ai_mesh_control/main_app/` |
| `backend/auth/` | `control/ai_mesh_control/auth/` |
| `backend/policy/` | `control/ai_mesh_control/policy/` |
| `backend/core/` (firewall, gateway keys, kill switch) | `control/ai_mesh_control/core/` |
| `backend/security_engines/` | `control/ai_mesh_control/security_engines/` |
| `backend/ws/` | `control/ai_mesh_control/ws/` |
| Exclude: `device/`, `agent_distribution/`, non-firewall apps | — |

## Phase 2 — Data plane (`gateway/`)

| Source | Destination |
|--------|-------------|
| `gateway/main.py`, `middleware.py`, `config.py` | `gateway/ai_mesh_gateway/` |
| `gateway/rag_pipeline/` | `gateway/ai_mesh_gateway/rag_pipeline/` |
| `gateway/policy_sync.py`, `policy_signing.py` | `gateway/ai_mesh_gateway/` |
| `shared/jobs/` | `shared/ai_mesh_shared/jobs/` |

## Phase 3 — Workers (`workers/`)

| Source | Destination |
|--------|-------------|
| `backend/core/tasks.py` | `workers/ai_mesh_workers/tasks/` |
| `backend/core/job_drainers.py` | `workers/ai_mesh_workers/drainers/` |
| `backend/policy/tasks.py` | `workers/ai_mesh_workers/tasks/policy.py` |
| `backend/mcp_connector/tasks.py` | `workers/ai_mesh_workers/tasks/mcp.py` |
| `backend/security_engines/tasks.py` | `workers/ai_mesh_workers/tasks/tier2.py` |

## Phase 4 — Frontend (`frontend/`)

| Source | Destination |
|--------|-------------|
| `frontend/src/pages/firewall/` | `frontend/src/pages/` |
| `frontend/src/components/firewall/` | `frontend/src/components/` |
| `frontend/src/hooks/useSimulatorEngine.js` | `frontend/src/hooks/` |
| Remove AIGuardX routes from `App.jsx` | New `frontend/src/App.jsx` firewall-only |

## Phase 5 — Optional service extraction

Move code from `gateway/` into `services/guardrails`, `services/mcp-broker`, etc., when SLO/scale triggers fire (see ARCHITECTURE.md).

## Environment rename

| Monorepo | Standalone |
|----------|------------|
| `ai_security` DB | `ai_mesh_firewall` |
| `AIGUARDX_*` URLs | `AI_MESH_*` |
| Compose project `ai_security` | `ai_mesh_firewall` |

## Verification gate per phase

1. `docker compose up` all services healthy
2. `pytest tests/unit` in this repo
3. Login + policy compile + gateway `/health` + simulator run
4. No imports from parent repo paths (enforced in CI)
