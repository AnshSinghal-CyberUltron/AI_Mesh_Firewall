# ZeroShield · OpenAI-SDK Reference Demo

A production-quality reference application that demonstrates **every major ZeroShield
capability through the unmodified OpenAI SDK**. A customer changes exactly two
things — `base_url` and `api_key` — and gets routing, model governance, input/output
validation, and RAG + MCP context security for free.

```python
from openai import OpenAI
client = OpenAI(
    api_key="ZEROSHIELD_API_KEY",
    base_url="https://aimeshgateway.zeroshield.ai/v1",
)
```

The demo **never imports a provider SDK** (no `anthropic`, no `google-genai`). The
only client constructed anywhere is `openai.OpenAI`, in one place:
`backend/zeroshield_client.py → make_client()`.

---

## Architecture

```
┌──────────────┐     OpenAI SDK only      ┌───────────────────────────────────────┐
│  Browser UI  │  ───────────────────────▶│  Demo backend (FastAPI)               │
│  (frontend/) │   /api/chat /rag /mcp     │  - client-side orchestration:         │
│              │   /route /validate /files │      retrieval, file text extraction, │
│  Live        │ ◀───────────────────────  │      context assembly                 │
│  Pipeline    │   content + pipeline_trace│  - ALL AI calls via openai.OpenAI ────┼──┐
│  Visualizer  │                           └───────────────────────────────────────┘  │
└──────────────┘                                                                       │
                                                                                       ▼
                          ┌───────────────────────────────────────────────────────────────┐
                          │  ZeroShield Gateway   https://aimeshgateway.zeroshield.ai/v1    │
                          │                                                                 │
                          │  auth → rate-limit → POLICY → INPUT SCAN → kill-switch →        │
                          │  MODEL ROUTING → model → OUTPUT GUARDRAIL                        │
                          │                                                                 │
                          │  returns standard OpenAI payload  +  zeroshield{} + pipeline_   │
                          │  trace{} (the demo renders these as the live visualizer)        │
                          └───────────────────────────────┬─────────────────────────────────┘
                                                          ▼
                                  BYOK providers (OpenAI / Anthropic / Gemini / OpenRouter)
```

**Division of responsibility** (this is the honest, real architecture):

| Concern | Owner |
|---|---|
| Retrieval / vector store, file text extraction, context assembly | **Demo client** (orchestration) |
| Routing, fallback, model governance, kill-switch | **Gateway** |
| Input scanning (PII, credentials, injection, policy) | **Gateway** |
| Output guardrails (PII/credential/IP redaction, hallucination/grounding) | **Gateway** |
| RAG-context poison scanning, MCP context governance | **Gateway** |
| The LLM call itself | **Gateway → BYOK provider** |

---

## Run it

```bash
cd examples/zeroshield-openai-demo/backend
pip install -r requirements.txt
export ZEROSHIELD_API_KEY="<your gateway key>"
export ZEROSHIELD_BASE_URL="https://aimeshgateway.zeroshield.ai/v1"
uvicorn main:app --port 8800
# open http://127.0.0.1:8800
```

Frontend tests (with the backend running):

```bash
NODE_PATH=/opt/homebrew/lib/node_modules node tests/playwright_demo.cjs
```

---

## Capability matrix (all via the OpenAI SDK)

| # | Capability | Endpoint | SDK call | Status |
|---|---|---|---|---|
| 1 | Chat (multi-turn, streaming) | `/api/chat`, `/api/chat/stream` | `chat.completions.create` (`stream=True`) | ✅ |
| 2 | RAG (grounded answers + poison scan) | `/api/rag/*` | `chat.completions.create` | ✅ |
| 3 | MCP context governance | `/api/mcp/query` | `responses`/`chat` + `extra_body.mcp_context` | ✅ |
| 4 | Multi-model routing + fallback | `/api/route` | `responses.create` + `extra_body.routing_preferences` | ✅ |
| 5 | File upload + analysis (PDF/DOCX/TXT/CSV) | `/api/files/analyze` | `chat.completions.create` | ✅ |
| 6 | Output validation (PII/cred/injection/policy) | `/api/validate` | `chat.completions.create` | ✅ |
| 7 | Routing/pipeline visualizer | every response | reads `pipeline_trace` + `zeroshield` from the OpenAI-compatible body | ✅ |
| 8 | Governance/observability + incident IDs | `/api/observability` | SDK-native `client.get("/observability")`; `request_id` = incident id | ✅ |
| — | Model discovery | `/api/models` | `models.list()` | ✅ |

See `reports/VALIDATION_REPORT.md` for the evidence behind every row, and
`sdk_examples.py` for the raw OpenAI-SDK snippets for each scenario.

---

## How the visualizer works (no custom protocol)

ZeroShield returns the **standard OpenAI payload** and rides extra fields on the same
JSON: `zeroshield` (verdict, routing, guard action) and `pipeline_trace` (the 9
firewall stages). The demo reads them via the SDK's raw-response accessor:

```python
raw = client.chat.completions.with_raw_response.create(model="auto", messages=msgs)
body = json.loads(raw.text)          # standard OpenAI fields + body["zeroshield"] + body["pipeline_trace"]
```

A vanilla OpenAI client that ignores these extra fields still works perfectly —
they are additive.
