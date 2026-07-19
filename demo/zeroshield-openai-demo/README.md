# ZeroShield OpenAI SDK Demo (legacy tree)

> **Canonical app:** [`examples/zeroshield-openai-demo/`](../../examples/zeroshield-openai-demo/)
> (local `:8180/demo/`, ECR `ai-mesh-demo`, prod nginx `/demo/`). Prefer that tree for all work.

Production-quality reference application proving that **all major ZeroShield capabilities**
are accessible through the **standard OpenAI Python SDK** — customers only change `base_url`
and `api_key`.

## RAG setup (one-time)

RAG requires Chroma, a vector collection policy, and an org vector provider:

```bash
# 1. Start Chroma
docker compose --profile chroma up -d chromadb

# 2. Bootstrap policies + provider (control must allow private Docker URLs — see docker-compose.override.yml)
python scripts/bootstrap_rag.py
```

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
# Adapter unit test (mcp_context passthrough)
pytest ../../gateway/ai_mesh_gateway/tests/test_responses_adapters.py -q

# Playwright (demo server must be running)
cd tests/playwright && npm init -y && npm i playwright
npx playwright test demo.spec.mjs
```

## Customer demo

See [docs/CUSTOMER_DEMO_SCRIPT.md](docs/CUSTOMER_DEMO_SCRIPT.md).

## Validation reports

- [docs/reports/E2E_VALIDATION.md](docs/reports/E2E_VALIDATION.md)
- [docs/reports/ROUTING_VALIDATION.md](docs/reports/ROUTING_VALIDATION.md)
- [docs/reports/RAG_VALIDATION.md](docs/reports/RAG_VALIDATION.md)
- [docs/reports/MCP_VALIDATION.md](docs/reports/MCP_VALIDATION.md)
- [docs/reports/OUTPUT_VALIDATION.md](docs/reports/OUTPUT_VALIDATION.md)
