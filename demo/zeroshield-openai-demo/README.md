# ZeroShield OpenAI SDK Demo

Production-quality reference application proving that **all major ZeroShield capabilities**
are accessible through the **standard OpenAI Python SDK** — customers only change `base_url`
and `api_key`.

## RAG setup (one-time)

RAG requires Chroma, a vector collection policy, and an org vector provider:

```bash
# 1. Start Chroma (image must match gateway chromadb client — see docker-compose.yml)
docker compose --profile chroma up -d chromadb

# 2. Bootstrap policies + provider (control must allow private Docker URLs — see docker-compose.override.yml)
python scripts/bootstrap_rag.py
```

If ingest returns `ingest_failed` with Chroma `KeyError('_type')`, your Chroma server image is too old for the gateway client — recreate with `docker compose --profile chroma up -d --force-recreate chromadb`.

If query returns `retrieval_error`, ensure the org **custom** provider URL matches a
Milvus-compatible endpoint, or configure Pinecone and use an existing collection like
`zeroshield-rag-e2e`.

## Quick start

```bash
cd demo/zeroshield-openai-demo
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # set ZEROSHIELD_API_KEY

# Start demo UI (http://127.0.0.1:8765)
python -m app.server
```

## Production readiness checklist

Before client demos or production rollout, verify:

1. `GET /api/readiness` returns `ok: true` and `models_healthy >= 1`.
2. No provider connectivity errors in gateway logs (TLS/certificate, timeout, unreachable host).
3. RAG backend (Chroma/custom provider) is reachable and ingestion succeeds.
4. Demo server and gateway clocks/network are stable (timeouts often indicate infrastructure drift).

If `/api/readiness` is degraded, fix provider connectivity first; the demo UI is resilient and will show friendly error hints, but upstream model calls will still fail until infrastructure is healthy.

If Chat returns **Provider key rejected** (HTTP 401), the org model connection likely has a bad BYOK key (a common mistake is pasting the admin password). Set `OPENAI_API_KEY` in the repo root `.env` and run:

```bash
python scripts/bootstrap_openai_models.py
```

**Production image:** ECR `ai-mesh-demo` is built from this directory (`demo/zeroshield-openai-demo`) via `infra/scripts/build-push-images.sh` and served at `/demo/` behind nginx Basic Auth.

## Architecture

```
Browser UI  →  FastAPI demo server  →  openai.OpenAI(base_url=ZeroShield /v1)
                                              ↓
                                    ZeroShield Gateway
                              (routing, RAG, MCP, guardrails, governance)
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## SDK-only guarantee

- **No** direct OpenAI/Anthropic/Bedrock SDK calls
- **No** custom ZeroShield client library for inference
- RAG ingest/query uses `client.post()` — the stock SDK HTTP transport
- All chat/responses use `client.chat.completions` / `client.responses`

See [docs/SDK_EXAMPLES.md](docs/SDK_EXAMPLES.md) for all six customer scenarios.

## Features demonstrated

| Tab | Gateway capability |
|-----|-------------------|
| Chat | Multi-turn, streaming, model selection |
| RAG | Ingest + query + grounded synthesis |
| MCP | `extra_body.mcp_context` injection |
| Routing | Auto route, sensitivity, routed model visibility |
| Files | PDF/DOCX/TXT/CSV upload → gateway analysis |
| Guardrails | Input/output block, verdict display |
| Scenarios | One-click SDK scenario runner |

## Testing

```bash
# Demo unit + MCP/routing/files/guardrails contract
pytest tests/test_status_reason.py tests/test_mcp_scenario.py tests/test_routing_scenario.py tests/test_files_scenario.py tests/test_guardrail_scenario.py -q

# Demo backend E2E (server must be on :8765; section E = routing matrix)
python tests/validation_backend.py

# Adapter + telemetry (gateway)
pytest ../../gateway/ai_mesh_gateway/tests/test_responses_adapters.py -q
pytest ../../gateway/ai_mesh_gateway/tests/test_context_source_telemetry.py -q

# Playwright (demo server must be running)
cd tests/playwright && npm init -y && npm i playwright
DEMO_URL=http://127.0.0.1:8765 npx playwright test demo.spec.mjs
```

### Pre-demo MCP checklist

1. `GET /api/readiness` → `ok: true`
2. `python tests/validation_backend.py` → section **F** green (benign + sensitive vectors)
3. MCP tab: benign vector → allow banner + pipeline; sensitive vector → block/redact banner

### Pre-demo routing checklist

1. `GET /api/readiness` → `ok: true`
2. `python tests/validation_backend.py` → section **E** green (standard + restricted + hipaa vectors)
3. Routing tab: prefs preview shows `enable_routing`; run → sidebar shows Requested/Routed/Decision source
4. Gateway gates: `pytest ../../gateway/ai_mesh_gateway/tests/test_routing_pool_hardening.py -q`

### Pre-demo Files checklist

1. `GET /api/readiness` → `ok: true`
2. `python tests/validation_backend.py` → section **I** green (benign + sensitive + partial vectors)
3. Files tab: select CSV → preview shows filename; analyze → summary + documents table (no raw JSON by default)
4. Pipeline sidebar populates after analyze

### Pre-demo Guardrails checklist

1. `GET /api/readiness` → `ok: true`
2. `python tests/validation_backend.py` → section **G** green (attack + safe + sensitive vectors)
3. Guardrails tab: attack vector → block banner + pipeline; safe vector → allow + output guard stage
4. Pipeline sidebar shows `input_scan` / `output_guardrail` on live runs

### Pre-demo SDK Scenarios checklist

1. `GET /api/readiness` → `ok: true`
2. `GET /api/sdk-scenarios` → six patterns listed
3. `python tests/validation_backend.py` → section **J** green (all six scenarios + stream)
4. RAG prerequisite: `python scripts/bootstrap_rag.py` (if section **J** rag check needs indexed docs)
5. SDK Scenarios tab: basic + guardrail buttons → friendly summary (not raw JSON); pipeline sidebar populates
6. CLI parity: `python scripts/sdk_examples.py` (scenario 5 uses `responses.create`)

## Customer demo

See [docs/CUSTOMER_DEMO_SCRIPT.md](docs/CUSTOMER_DEMO_SCRIPT.md).

## Validation reports

- [docs/reports/E2E_VALIDATION.md](docs/reports/E2E_VALIDATION.md)
- [docs/reports/ROUTING_VALIDATION.md](docs/reports/ROUTING_VALIDATION.md)
- [docs/reports/RAG_VALIDATION.md](docs/reports/RAG_VALIDATION.md)
- [docs/reports/MCP_VALIDATION.md](docs/reports/MCP_VALIDATION.md)
- [docs/reports/FILE_VALIDATION.md](docs/reports/FILE_VALIDATION.md)
- [docs/reports/GUARDRAIL_VALIDATION.md](docs/reports/GUARDRAIL_VALIDATION.md)
- [docs/reports/SDK_SCENARIOS_VALIDATION.md](docs/reports/SDK_SCENARIOS_VALIDATION.md)
- [docs/reports/OUTPUT_VALIDATION.md](docs/reports/OUTPUT_VALIDATION.md)
