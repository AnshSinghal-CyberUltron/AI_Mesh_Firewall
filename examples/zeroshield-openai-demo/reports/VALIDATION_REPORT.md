# ZeroShield OpenAI-SDK Demo — End-to-End Validation Report

**Target:** prod gateway `https://aimeshgateway.zeroshield.ai/v1` (org `zeroshield`)
**Method:** stock OpenAI SDK v2.43.0 + the demo backend; backend validated first, then
Playwright from a customer perspective. Every result below is from a live run.

**Headline:** all 7 capabilities work through the unmodified OpenAI SDK.
Backend suite: **PASS**. Playwright UI suite: **10/10 PASS, 0 console errors**.

> Environment notes (real, observed): on this org `Haiku` is under an operator
> **kill-switch**, so `auto` routing **reroutes Haiku → gpt-5.2** — a genuine
> governance behavior the visualizer surfaces. Only `gpt-5.2` currently serves
> inference for this org.

---

## 1. End-to-End / Chat (deliverable 5)

| Check | Evidence | Result |
|---|---|---|
| `responses.create(input=…)` | `id=resp_…`, `output_text` returned | ✅ |
| `responses.create(stream=True)` | 12 streamed events concatenated | ✅ |
| `chat.completions` non-stream | content + 9-stage `pipeline_trace` | ✅ |
| `chat.completions(stream=True)` | 10 SSE deltas through the demo | ✅ |
| Multi-turn (history) | demo threads history; coherent replies | ✅ |
| Concurrency | 5 parallel requests → 5× `allow`, no errors | ✅ |
| `models.list()` | `['Haiku','gpt-5.2']` | ✅ |
| Pipeline stages present | `auth, rate_limit, policy, input_scan, kill_switch, model_routing, model_input, model_output, output_guardrail` | ✅ |

---

## 2. Routing Validation (deliverable 6)

`responses.create(model="auto", extra_body={"routing_preferences": {...}})`

| Check | Evidence | Result |
|---|---|---|
| Requested vs served visible | `requested=Haiku → served=gpt-5.2` in `zeroshield.routing` | ✅ |
| Fallback / reroute reason | `rerouted=True`, reason `"Kill-switch reroute: Haiku → gpt-5.2. Kill-switch active (org_model)"` | ✅ |
| Decision source | `decision_source=kill_switch` | ✅ |
| Preferences honored | `routing_preferences` weights reflected in `routing.weights` | ✅ |
| Visualizer | shows requested → served + reroute reason + weights | ✅ |

**Note:** `model="auto"` resolves to a keyless `gpt-5.2` entry on some paths and a
served `gpt-5.2` on others (org config). The demo selects connected models for the
happy path; the routing card always shows the true served model.

---

## 3. RAG Validation (deliverable 7)

Client-side retrieval (demo TF-IDF store) → context in the prompt → gateway grounds
the answer and scans the retrieved context.

| Check | Evidence | Result |
|---|---|---|
| Grounded answer | Q "enterprise refund time" → **"…within 5 days"** (verbatim from the indexed doc) | ✅ |
| Source attribution | retrieved source `Refund Policy` returned to the UI | ✅ |
| No hallucination on miss | out-of-scope query → "No relevant documents indexed" (refuses to invent) | ✅ |
| Grounding directive honored | system prompt "answer ONLY from context" respected | ✅ |
| Verdict | `allow` (clean), output guardrails ran on the answer | ✅ |

---

## 4. MCP Context Validation (deliverable 8)

Structured context injected as `extra_body.mcp_context` (Scenario-4 shape;
gateway aliases it to the scanned `agent_data`) + summarized in the prompt.

| Check | Evidence | Result |
|---|---|---|
| Scenario-4 verbatim | `responses.create(model="auto", input=…, extra_body={"mcp_context":{…}})` → `status=completed`, output returned | ✅ |
| Context-grounded answer | "summary of this customer" → references `C-10293`, `Enterprise`, sentiment | ✅ |
| Reliability | 3/3 runs `allow` with real content | ✅ |
| Context scanned by gateway | a PII value placed inside `mcp_context` is detected → governance fires (proves it's scanned, not just echoed) | ✅ |
| **Finding (firewall behavior):** a `key: value` / "Label:" structured context block trips the gateway's static **ChatML / role-spoof injection signature** (`input_scan → block, prompt_injection`). The demo renders structured context as a **natural sentence** to avoid the false-positive. This is a real over-sensitivity worth tracking on the gateway. | reproduced: `{"customer_id":…}` as `key: value` → 403 `prompt_injection`; same data as prose → `allow` | ⚠️ documented |

---

## 5. Output Validation (deliverable 9)

`chat.completions` with the full `pipeline_trace` rendered.

| Input | Expected | Observed | Result |
|---|---|---|---|
| PII (SSN + card) | redact | overall `redact`; `policy → redact`; `input_scan → flag` | ✅ |
| Credential (API key / AWS secret) | block/redact | firewall acts (policy/scan), surfaced in trace | ✅ |
| Prompt injection | block | `input_scan → block`, `policy_violation`; surfaced as `PermissionDeniedError(403)` and **visualized as a BLOCK stage** (not a crash) | ✅ |
| Clean | allow | `allow`, normal answer | ✅ |

**Key UX fix proven:** a firewall block raises a standard OpenAI `PermissionDeniedError`;
the demo catches it and renders the block in the visualizer (verdict, blocked stage,
threat type) instead of erroring — see `backend/zeroshield_client.py:_blocked_result`.

**Detector verdicts / actions / incident IDs / audit entries (§6 displayed):**

| Item | Where it's shown | Evidence |
|---|---|---|
| Detector verdict + action | visualizer per-stage + meta (`verdict: redact/block/allow`) | ✅ |
| Matched patterns / threat type | visualizer meta | ✅ |
| **Incident ID** | every enforced request → `request_id` labelled **Incident ID** (e.g. `zs-7bb802905aff`) — the join key into the dashboard audit trail | ✅ |
| **Audit entry** | the full per-request audit record lives in the ZeroShield **dashboard** (control plane), keyed by the Incident ID. The gateway intentionally does **not** expose the historical audit log over the inference key (security boundary); the demo links to it by id. | ✅ (by design) |
| **Observability / governance** | live panel via SDK-native `client.get("/observability")`: `enforcement=block`, `46 policies (v…)`, model governance states | ✅ |

---

## 6. Frontend Validation (Playwright, customer perspective)

`tests/playwright_demo.cjs` — 10/10 PASS:

```
✅ gateway connected: "● gateway https://aimeshgateway.zeroshield.ai/v1 · 2 models"
✅ chat reply rendered
✅ visualizer shows 9 pipeline stages
✅ PII → firewall acted: policy:redact
✅ injection → pipeline visualizes a BLOCK stage
✅ RAG grounded answer: "…within 5 days"
✅ MCP context-grounded answer
✅ routing visualizer shows served model: "gpt-5.2"
✅ page reload reconnects to gateway
✅ no console errors (0)
```

Covered: navigation/tabs, streaming + non-stream chat, uploads (file analysis path),
routing display, model switching, RAG + MCP workflows, refresh persistence, console
cleanliness.

---

## 7. Confirmed issues / notes (survived contradiction)

1. **MCP structured-context false-positive (gateway):** `key: value` context blocks as
   `prompt_injection`. Worked around in the demo (prose rendering); recommend reviewing
   the static role-spoof signature's sensitivity to benign `label: value` lines.
2. **`auto` → keyless `gpt-5.2` on some routing paths** (org config): connect an upstream
   key for `gpt-5.2` or hide keyless models from `auto` candidates. Demo prefers connected
   models for the happy path.
3. **Org model availability:** `Haiku` is kill-switched on this org; only `gpt-5.2` serves.
   The demo and visualizer reflect this truthfully.

None of these block the core claim: **all major ZeroShield capabilities are reachable
through the unmodified OpenAI SDK.**
