# SDK Scenarios Validation Report

## Objective

Prove the customer integration catalog message:

**Your existing OpenAI SDK code works unchanged — you only change `base_url` and `api_key`.**

The SDK Scenarios tab is a one-click replay of all six stock patterns with friendly verdict copy (not a raw JSON debugger).

## In scope / out of scope

| In scope | Out of scope |
|----------|--------------|
| Six catalog scenarios: basic, stream, rag, mcp, routing, guardrail | Files/multipart (Scenario 7) |
| `GET /api/sdk-scenarios` catalog bootstrap | New gateway API surface |
| `sdk_scenario` metadata echo on responses | Deep vector-store exploration (RAG tab) |
| CLI parity via `scripts/sdk_examples.py` | Custom provider SDKs |

## Six-pattern catalog

| # | ID | API path | SDK pattern |
|---|-----|----------|-------------|
| 1 | `basic` | `POST /api/respond` | `responses.create` |
| 2 | `stream` | `POST /api/respond/stream` | `responses.create(..., stream=True)` |
| 3 | `rag` | `POST /api/rag/query` + synthesize | RAG query + governed synthesis |
| 4 | `mcp` | `POST /api/respond` | `extra_body.mcp_context` |
| 5 | `routing` | `POST /api/respond` | `extra_body.routing_preferences` |
| 6 | `guardrail` | `POST /api/respond` | `responses.create` (injection block) |

Canonical definitions live in `app/sdk_scenarios.py` (`SDK_SCENARIO_CATALOG`).

## Prerequisites

- Gateway running at `http://127.0.0.1:8300/v1`
- Demo app at `http://127.0.0.1:8765`
- `ZEROSHIELD_API_KEY` in `demo/.env`
- **RAG scenario:** run `python scripts/bootstrap_rag.py` so `demo_knowledge` is ingested (UI shows bootstrap hint on `rag_access_denied`)

## Customer demo flow

1. Open **SDK Scenarios** tab — preview shows selected pattern one-liner
2. Run **1. Basic** → title + verdict + answer snippet + SDK pattern line
3. Run **2. Streaming** → streamed text summary (pipeline in sidebar)
4. Run **6. Guardrail** → governed block on attack preset
5. Message: *"Same SDK calls your team already uses — only the base URL changes."*

## Executable gates

```bash
# Unit contract
cd demo/zeroshield-openai-demo
pytest tests/test_sdk_scenarios.py -q

# Backend E2E section J (demo on :8765)
DEMO_URL=http://127.0.0.1:8765 python tests/validation_backend.py

# CLI parity (scenario 5 uses responses.create + routing_preferences)
python scripts/sdk_examples.py

# Playwright
cd tests/playwright
DEMO_URL=http://127.0.0.1:8765 npx playwright test demo.spec.mjs -g "sdk scenario"
```

## Pass criteria

- All six scenario buttons return structured responses (no demo 500)
- `sdk_scenario`, `sdk_label`, `sdk_pattern` present on scenario API responses
- UI primary panel shows customer summary (not raw JSON)
- Stream path completes with `[DONE]` and delta frames
- CLI scenario 5 matches UI routing pattern (`responses.create`)

## Rollback triggers

| Symptom | Action |
|---------|--------|
| Any scenario button crashes or returns unstructured 500 | Block release |
| `sdk_scenario` missing on responses | Revert `sdk_scenarios.py` + scenario helpers |
| CLI scenario 5 diverges from UI routing again | Revert `sdk_examples.py` + catalog |
| UI shows raw JSON as primary output | Revert `app.js` `renderScenarioOutput` + catalog together |

Revert unit: `app/sdk_scenarios.py` + `gateway_client.py` scenario helpers + `web/app.js` renderer — keep catalog and UI in sync.
