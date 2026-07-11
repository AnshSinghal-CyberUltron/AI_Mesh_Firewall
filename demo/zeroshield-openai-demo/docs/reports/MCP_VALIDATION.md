# MCP Validation Report

## What is validated

- `extra_body.mcp_context` reaches gateway input scanner via `responses.create`
- Demo MCP tab sends explicit context vectors (benign + sensitive)
- Response includes `status_reason`, `pipeline`, and echoed `mcp_context`
- SOC telemetry tags SDK-injected context with `metadata.context_source=mcp` (Chat lane)

## SDK shape (Scenario 4)

```python
client.responses.create(
    model="auto",
    input="Create a customer summary",
    extra_body={"mcp_context": {"customer_id": "C-123", "profile": {...}}},
)
```

## Demo status reasons

| Code | Meaning |
|------|---------|
| `allowed` | MCP context passed all stages |
| `mcp_context_redacted` | Sensitive MCP fields redacted; request continued |
| `mcp_event_blocked` | MCP context blocked during input analysis |
| `blocked_policy` | Generic policy block (chat path semantics) |

## Pass criteria

- Benign MCP context → allow + pipeline visible
- Sensitive MCP context (SSN) → `mcp_event_blocked` or `mcp_context_redacted`
- Request-body `mcp_context` is honored (not replaced by server defaults)
- Input scan stage appears in pipeline visualizer when trace is present
- **SOC traceability:** enforcement events stay in the **Chat** M2 lane but carry
  `metadata.context_source=mcp`. Module 2 **MCP lane** KPIs are for live `tools/call` traffic only (Module 1.4).

## Test vectors

| Input | Expected |
|-------|----------|
| Benign CRM profile | `allowed` |
| MCP context with fake SSN | `mcp_context_redacted` or `mcp_event_blocked` |
| Custom `mcp_context` in API body | echoed in response; used by gateway |
| Oversized context | 413 or truncation per org limits |

## Executable gates

```bash
# Unit
pytest tests/test_status_reason.py tests/test_mcp_scenario.py -q

# Backend E2E (demo server on :8765)
python tests/validation_backend.py

# UI E2E
cd tests/playwright && DEMO_URL=http://127.0.0.1:8765 npx playwright test demo.spec.mjs

# Gateway MCP integrity
pytest ../../gateway/ai_mesh_gateway/tests/test_responses_adapters.py -q
pytest ../../gateway/ai_mesh_gateway/tests/test_context_source_telemetry.py -q
```

## Rollback triggers

- MCP tab shows generic `blocked_policy` when server sent `mcp_event_blocked`
- Request `mcp_context` ignored (response context does not match request)
- Pipeline panel empty after successful MCP run
- Sensitive vector returns `allowed` with full SSN echoed in model output (governance regression)
