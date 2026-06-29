# Architecture — ZeroShield OpenAI SDK Demo

## Purpose

Reference application for customer demonstrations and end-to-end gateway validation.
Proves the **base URL + API key swap** integration model.

## Components

```mermaid
flowchart TB
  subgraph client [Demo Application]
    UI[Web UI — web/]
    API[FastAPI — app/server.py]
    ZSC[ZeroShieldClient — app/gateway_client.py]
  end
  subgraph sdk [Stock OpenAI SDK]
    OAI[openai.OpenAI]
  end
  subgraph gw [ZeroShield Gateway]
    CHAT[/v1/chat/completions]
    RESP[/v1/responses]
    RAG[/v1/rag/*]
  end
  UI --> API
  API --> ZSC
  ZSC --> OAI
  OAI --> CHAT
  OAI --> RESP
  OAI --> RAG
```

## Request flow (chat / responses)

1. User action in browser
2. FastAPI endpoint builds messages / input
3. `ZeroShieldClient` calls `client.responses.create` or `client.chat.completions.create`
4. Gateway runs: auth → rate limit → policy → input scan → routing → upstream → output guard
5. Response includes `zeroshield` metadata on `model_extra`
6. `build_pipeline_view()` renders customer-visible pipeline stages

## RAG flow

1. User pastes document text or uploads file
2. Demo extracts text locally (pypdf/python-docx — not sent to providers directly)
3. `client.post("/rag/ingest", ...)` indexes via gateway
4. `client.post("/rag/query", ...)` retrieves with firewall enforcement
5. Retrieved chunks prepended to prompt → `responses.create` for synthesis

## MCP flow

MCP/customer context is injected via SDK `extra_body`:

```python
client.responses.create(
    model="auto",
    input="Create a customer summary",
    extra_body={"mcp_context": {"customer_id": "123", "profile": {...}}},
)
```

Gateway treats `mcp_context` identically to `agent_data` for input scanning and governance.

## Routing visibility

Gateway returns routing metadata in `zeroshield.routing`:

- `requested_model` / `selected_model` / `fallback_model`
- `reason` — adjudicator explanation

The demo sidebar visualizer displays these fields on every successful request.

## Security boundary

| Layer | Responsibility |
|-------|----------------|
| Demo server | UX, file extraction, session history |
| OpenAI SDK | HTTP transport, retries, parsing |
| ZeroShield Gateway | All security, routing, RAG, MCP governance |

The demo server never holds provider API keys — only the ZeroShield gateway key.
