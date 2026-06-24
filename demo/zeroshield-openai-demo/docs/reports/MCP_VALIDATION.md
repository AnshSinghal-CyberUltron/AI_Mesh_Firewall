# MCP Validation Report

## What is validated

- `extra_body.mcp_context` reaches gateway input scanner
- Context appears in MCP governance telemetry
- Demo MCP tab produces response with pipeline metadata

## SDK shape (Scenario 4)

```python
client.responses.create(
    model="auto",
    input="Create a customer summary",
    extra_body={"mcp_context": {"customer_id": "123", "profile": {...}}},
)
```

## Adapter passthrough

Unit test `test_responses_to_chat_passthrough_mcp_and_routing` confirms
`responses_to_chat()` preserves `mcp_context`, `agent_data`, and `routing_preferences`.

## Pass criteria

- Request succeeds with MCP payload
- Input scan stage shows in pipeline visualizer
- PII/secrets in MCP context trigger block or redact

## Test vectors

| Input | Expected |
|-------|----------|
| Benign CRM profile | allow |
| MCP context with fake SSN | redact or block |
| Oversized context | 413 or truncation per org limits |
