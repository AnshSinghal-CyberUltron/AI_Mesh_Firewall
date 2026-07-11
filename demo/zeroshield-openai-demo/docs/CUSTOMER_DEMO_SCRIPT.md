# Customer Demo Script — ZeroShield OpenAI SDK

**Duration:** 15–20 minutes  
**Audience:** Technical buyers, platform engineers, security leaders  
**Prerequisite:** Demo running at `http://127.0.0.1:8765` with valid `ZEROSHIELD_API_KEY`

---

## 1. Opening (2 min)

> "ZeroShield is a drop-in security and governance layer for LLM applications.
> You keep your existing OpenAI SDK code — you only change `base_url` and `api_key`."

Show `docs/SDK_EXAMPLES.md` Scenario 1 code snippet.

---

## 2. Chat + streaming (3 min)

1. Open **Chat** tab
2. Select **Auto Route** model
3. Ask: *"Explain how a reverse proxy secures LLM traffic."*
4. Enable **Stream response**, send again
5. Point to **Request Pipeline** sidebar — auth, input scan, routing, output validation

**Talk track:** Every request passes through the full mesh firewall before reaching any provider.

---

## 3. Multi-model routing (3 min)

1. Open **Routing** tab
2. Prompt: *"Write Python to parse JSON safely."*
3. Change sensitivity to **restricted** → Run
4. Show **Requested vs Routed model** and routing reason in sidebar

**Talk track:** Routing is policy-aware — compliance and sensitivity drive model selection.

---

## 4. MCP context (2 min)

1. Open **MCP Context** tab
2. Run default prompt
3. Show that `mcp_context` is injected via `extra_body` — no custom SDK

**Talk track:** CRM/ticket/catalog context is scanned and governed like any other input.

---

## 5. RAG knowledge base (3 min)

1. Open **RAG** tab
2. Paste a short policy document → **Index Document**
3. Query: *"What are the key policy points?"*
4. Show retrieval + synthesized answer

**Talk track:** Vector ingest and query are firewall-mediated — injection and cross-tenant access are blocked.

---

## 6. Guardrails (2 min)

1. Open **Guardrails** tab
2. Click **Send (expect block)** — injection prompt
3. Show block verdict, request ID, pipeline stages
4. Click **Send Safe Prompt** — contrast allow path

---

## 7. File analysis (2 min)

1. Upload a PDF or TXT
2. **Analyze via Gateway**
3. Show gateway-processed summary (not direct provider upload)

---

## 8. SDK scenarios + close (2 min)

1. Open **SDK Scenarios** → preview shows SDK pattern one-liner per button
2. Run scenarios **1 (Basic)**, **2 (Streaming)**, and **6 (Guardrail)** for the live proof
3. Emphasize: *"This demo app uses zero provider SDKs. Your integration looks identical."*
4. Reference [reports/SDK_SCENARIOS_VALIDATION.md](reports/SDK_SCENARIOS_VALIDATION.md) for release gates

**Close:** Hand off `docs/SDK_EXAMPLES.md` and gateway API key provisioning docs.

---

## Objection handling

| Objection | Response |
|-----------|----------|
| "We use LangChain/LlamaIndex" | Point OpenAI-compatible base URL at ZeroShield — same pattern |
| "Does streaming work?" | Live demo Scenario 2 |
| "What about tool calling?" | Supported via chat/responses; gateway scans tool args |
| "Latency overhead?" | Show `processing_time_ms` in pipeline sidebar |
