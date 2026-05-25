# gateway (data plane)

FastAPI L7 AI Mesh Firewall — auth, rate limits, policy enforcement, RAG/MCP/vector routing, streaming.

**Does not** own policy authoring or Postgres writes on hot path.

## Extract from monorepo

`../gateway/` → this package (`ai_mesh_gateway`).

## Run locally

```bash
uv run uvicorn ai_mesh_gateway.main:app --host 0.0.0.0 --port 8300 --reload
```

## Depends on

- Redis (policy cache, auth, rate limits)
- Control API (bootstrap only; not per-request)
- Optional: `services/guardrails`, `services/mcp-broker`, `services/vector-retrieval`
