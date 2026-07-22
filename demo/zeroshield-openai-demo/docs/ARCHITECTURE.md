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

MCP in the demo is **context governance** on the normal `responses.create` path — not a separate model API.

1. MCP tab selects a context vector (benign CRM or sensitive SSN) and sends `mcp_context` in the request body.
2. Demo server forwards via `ZeroShieldClient.scenario_mcp()` → `responses.create(..., extra_body={"mcp_context": ...})`.
3. Gateway scans nested MCP strings with the same input/policy boundary as user prompts.
4. Response includes `status_reason`, `pipeline`, and echoed `mcp_context` for auditability.

```python
client.responses.create(
    model="auto",
    input="Create a customer summary",
    extra_body={"mcp_context": {"customer_id": "C-123", "profile": {...}}},
)
```

Gateway treats `mcp_context` identically to `agent_data` for input scanning. Telemetry for this path stays on the **Chat** lane with `metadata.context_source=mcp` (distinct from Module 1.4 live MCP tool calls).

## Files flow

Files in the demo are **document-to-prompt** analysis — not a gateway file API.

1. User uploads PDF/DOCX/TXT/CSV in the **Files** tab.
2. Demo server extracts text locally (`app/extractors.py`) — binaries never leave the app as uploads.
3. Extracted text is combined into an analysis prompt and sent via `ZeroShieldClient.scenario_files_analyze()` → `responses.create`.
4. Gateway runs the full policy/input-scan/routing/output-guard chain on the document text.
5. API returns `files_manifest` (name, status, char count only) plus `analysis` with `status_reason` and `pipeline`.

```python
# Customer pattern: extract locally, govern via responses.create
response = client.responses.create(
    model="auto",
    input=f"Summarize this document:\n\n{extracted_text}",
)
```

OpenAI `/v1/files` is **not** implemented on the gateway (intentional 404). Use local extraction + prompt instead.

## Guardrails flow

Guardrails demonstrate **input blocking** and **output validation** on the standard chat/responses path.

1. User selects a test vector (attack, sensitive, safe) in the **Guardrails** tab.
2. Demo server calls `scenario_guardrail_probe()` → `responses.create`.
3. Gateway runs input scan; blocks injection before upstream when policy matches.
4. If allowed, upstream model runs; output guard scans the response.
5. Response includes `status_reason`, `pipeline`, and echoed `guardrail_vector`.

```python
# Customer pattern: same SDK call, gateway enforces policy
response = client.responses.create(model="auto", input=user_prompt)
# Inspect response.model_extra["zeroshield"] or handle block exceptions
```

## Routing visibility

Gateway returns routing metadata in `zeroshield.routing`:

- `requested_model` / `selected_model` / `fallback_model`
- `reason` — adjudicator explanation

The demo sidebar visualizer displays these fields on every successful request.

## SDK Scenarios flow

The **SDK Scenarios** tab is the customer-facing integration catalog — one-click replays of all six stock OpenAI SDK patterns.

1. UI loads catalog metadata from `GET /api/sdk-scenarios` (labels + `sdk_pattern`; request bodies fall back to `SDK_SCENARIO_CATALOG` in `app/sdk_scenarios.py`).
2. Button click dispatches to the same demo API paths as the feature tabs (`/api/respond`, `/api/respond/stream`, `/api/rag/query`).
3. `ZeroShieldClient` scenario helpers attach `sdk_scenario`, `sdk_label`, and `sdk_pattern` via `attach_sdk_scenario_meta()`.
4. `renderScenarioOutput()` shows verdict + summary in the main panel; pipeline evidence stays in the sidebar.

```text
SDK Scenarios UI → demo API → ZeroShieldClient → openai.OpenAI → gateway
                                      ↓
                         sdk_scenario metadata + status_reason
```

See [reports/SDK_SCENARIOS_VALIDATION.md](reports/SDK_SCENARIOS_VALIDATION.md) for the section **J** backend matrix and Playwright gates.

## Security boundary

| Layer | Responsibility |
|-------|----------------|
| Demo server | UX, file extraction, session history |
| OpenAI SDK | HTTP transport, retries, parsing |
| ZeroShield Gateway | All security, routing, RAG, MCP governance |

The demo server never holds provider API keys — only the ZeroShield gateway key.
