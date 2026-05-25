# Repository layout

```
AI_Mesh_Firewall/
├── gateway/                 # Data plane (FastAPI) — extract from ../gateway
├── control/                 # Control plane (Django modular monolith) — extract from ../backend
├── workers/                 # Celery worker runtime — extract from ../backend/core/tasks
├── frontend/                # Operator UI — extract firewall pages from ../frontend
├── services/
│   ├── guardrails/          # Input/output scan service (Phase 2 extract)
│   ├── mcp-broker/          # MCP mediation (Phase 2 extract)
│   ├── vector-retrieval/    # Vector query + poison checks (Phase 2 extract)
│   └── telemetry-ingest/    # High-volume event sink (Phase 2 extract)
├── shared/                  # Cross-service Python + contracts
├── infra/                   # Docker, Terraform, runbooks
├── tests/                   # unit / integration / load / security
├── docs/                    # Architecture, migration, contracts
├── scripts/                 # Dev and ops helpers
├── docker-compose.yml
├── docker-compose.prod.yml
└── Makefile
```

## What stays in parent `AI_Security` repo

- `agent/` / endpoint distribution
- AIGuardX-only modules (behavioral analytics, device fleet, etc.)
- Non-firewall frontend routes

## Package naming

| Path | Python package |
|------|----------------|
| `gateway/` | `ai_mesh_gateway` |
| `control/` | `ai_mesh_control` |
| `workers/` | `ai_mesh_workers` |
| `shared/` | `ai_mesh_shared` |
