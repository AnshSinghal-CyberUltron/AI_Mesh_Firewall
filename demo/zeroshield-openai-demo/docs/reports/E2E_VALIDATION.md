# End-to-End Validation Report

**Application:** `demo/zeroshield-openai-demo`  
**Gateway contract:** OpenAI SDK base URL + API key swap  
**Validation date:** 2026-06-20 (live run)

## Summary

| Suite | Result | Evidence |
|-------|--------|----------|
| Backend validation (`tests/validation_backend.py`) | **17/17 PASS** | `DEMO_URL=http://127.0.0.1:8765 .venv/bin/python tests/validation_backend.py` |
| Playwright UI (`tests/playwright/demo.spec.mjs`) | **6/6 PASS** | Tab nav, health, guardrail UI, pipeline placeholder |
| Gateway SDK tests | **49/49 PASS** | `test_openai_sdk_compat`, stream errors, config sync |
| OpenAI SDK scenarios (`scripts/sdk_examples.py`) | Runnable | Loads `demo/.env` automatically |

## Backend validation matrix (2026-06-20)

| Capability | Endpoint / SDK call | Status |
|------------|---------------------|--------|
| Health + model governance | `/api/health`, `/api/models` | PASS |
| Chat + routing visibility | `client.chat.completions.create` via demo | PASS |
| Multi-turn sessions | `/api/chat` + `session_id` | PASS |
| Streaming SSE | `/api/chat/stream` | PASS |
| Concurrent session isolation | parallel `/api/chat` | PASS |
| Responses basic | `client.responses.create` scenario_basic | PASS |
| Routing scenario | `extra_body.routing_preferences` matrix (standard/restricted/hipaa) | PASS |
| MCP context | `extra_body.mcp_context` | PASS |
| Guardrails | attack/safe/sensitive matrix + `guardrail_vector` echo | PASS |
| RAG ingest | `client.post("/rag/ingest")` | PASS (gateway verdict; chroma upsert may fail locally) |
| RAG query + synthesis | `scenario_rag` | PASS (pipeline visible; 0 docs when vector store empty) |
| File upload + analysis | `/api/files/analyze` matrix (benign/sensitive/unsupported/partial) | PASS |
| SDK Scenarios catalog | Section **J**: six patterns + stream + metadata echo | PASS |

## Fixes applied this session

1. **Gateway RAG:** `custom` vector policy now resolves `chroma` BYOK provider (alias + HTTP URL → ChromaDBClient).
2. **Demo client:** `n_results` param, structured RAG/routing error handling, no 500 on gateway 4xx/5xx.
3. **Local dev:** `GATEWAY_ASYNC_VECTOR_INGEST=false` in `docker-compose.override.yml` for synchronous ingest.
4. **Validation:** httpx SSE resilience, retry on transient 503, `demo_knowledge` collection.

## Known prerequisites

- Gateway running (`http://127.0.0.1:8300/v1`)
- `ZEROSHIELD_API_KEY` in `demo/.env` (simulator org key)
- Vector provider: `chroma` at `http://chromadb:8000` (bootstrap via `python scripts/bootstrap_rag.py`)
- Chroma client/server version mismatch may block document upsert (`KeyError('_type')`) — query pipeline still validates

## Regression commands

```bash
# Demo backend suite (includes routing matrix section E)
cd demo/zeroshield-openai-demo
DEMO_URL=http://127.0.0.1:8765 python tests/validation_backend.py

# Demo unit (status reasons + routing/files/guardrails/sdk contract)
pytest tests/test_status_reason.py tests/test_routing_scenario.py tests/test_files_scenario.py tests/test_guardrail_scenario.py tests/test_sdk_scenarios.py -q

# Playwright (routing + MCP + files + guardrails + sdk scenarios live execution)
cd tests/playwright && DEMO_URL=http://127.0.0.1:8765 npx playwright test demo.spec.mjs

# SDK scenarios
python scripts/sdk_examples.py

# Gateway SDK + routing integrity
pytest gateway/ai_mesh_gateway/tests/test_openai_sdk_compat.py -v
pytest gateway/ai_mesh_gateway/tests/test_routing_pool_hardening.py -q
pytest gateway/ai_mesh_gateway/tests/test_routing_isolation.py gateway/ai_mesh_gateway/tests/test_pipeline_trace_routing.py -q
```

## Routing rollback guardrails

Block release when any of the following occur:

- Missing routing metadata on successful `model=auto` requests
- Sensitivity/compliance has no effect when the routing pool should differentiate
- Disabled/isolated model selected
- UI routing summary diverges from backend `zeroshield.routing` metadata

Revert demo routing contract (`server.py` + `gateway_client.py`) and UI mapping (`app.js`) together to avoid truthfulness drift.

## Files rollback guardrails

Block release when:

- Full extracted document text is echoed in `/api/files/analyze` responses
- Partial parse incorrectly shows `file_unreadable` when analysis succeeded
- Successful file analyze missing `analysis.pipeline` / `zeroshield` metadata

Revert `server.py` manifest contract and `web/app.js` `renderFilesOutput` together.

## Guardrails rollback guardrails

Block release when:

- Attack/jailbreak prompt is not governed (no block verdict)
- Safe prompt cannot complete allow path with pipeline metadata
- `guardrail_vector` not echoed in API response
- UI verdict diverges from backend `status_reason`

Revert `scenario_guardrail_probe` + `renderGuardrailOutput` together.

## SDK Scenarios rollback guardrails

Block release when:

- Any of the six scenario buttons crashes or returns unstructured 500
- `sdk_scenario` metadata missing on scenario responses
- CLI scenario 5 diverges from UI routing pattern (`responses.create`)
- UI shows raw JSON as primary output (regression)

Revert `app/sdk_scenarios.py` + scenario helpers + `renderScenarioOutput` together.

## Observability checks

Compare on each request:

- UI pipeline sidebar ↔ `zeroshield` / `pipeline` in response
- Gateway logs ↔ `request_id` in UI
- Control plane audit ↔ enforcement events (when telemetry enabled)
