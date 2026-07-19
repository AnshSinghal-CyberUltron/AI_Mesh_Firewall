# Customer Demo Script — ZeroShield via the OpenAI SDK

**Duration:** ~10 minutes. **Goal:** prove that a customer keeps their OpenAI code and
gains routing, governance, and input/output security by changing two lines.

**Setup (before the call):** backend running on `:8800`, a document handy, the browser
open to `http://127.0.0.1:8800`.

---

### 0 · The two-line pitch (30s)
Show `sdk_examples.py` top:
```python
client = OpenAI(api_key="ZEROSHIELD_API_KEY",
                base_url="https://aimeshgateway.zeroshield.ai/v1")
```
> "That's the entire integration. Same OpenAI SDK, same `responses.create` /
> `chat.completions`. Everything you're about to see is the gateway — your code
> doesn't change."

Point at the top bar: **"● gateway … · 2 models"** — live connection.

---

### 1 · Chat + the live pipeline (90s)
- **Chat** tab → ask *"Explain zero-trust security in 2 sentences."*
- As it answers, point to the **Request Pipeline** panel on the right:
  *auth → policy → input scan → routing → model → output guardrail.*
> "Every request is inspected on the way in and on the way out. This is the same
> response your OpenAI SDK already parses — we just add a trace."
- Toggle **stream** on, ask again → tokens stream live.

---

### 2 · Multi-model routing & governance (90s)
- **Routing** tab → prompt *"Write a haiku about secure AI."* → **Route request**.
- In the visualizer: **Requested `Haiku` → Served `gpt-5.2` · rerouted**.
> "We asked for one model; ZeroShield rerouted because an operator kill-switched it.
> Your app didn't crash or know — governance happened at the gateway."
- Slide the **latency/cost** weights → re-run → show the weights in the routing card.

---

### 3 · Output validation — the firewall in action (2 min)
- **Output Validation** tab.
- Click **PII (SSN + card)** preset → **Send through firewall**.
  → Visualizer: **policy → REDACT**. *"Sensitive data is redacted before it ever
  reaches the model — and on the way back out."*
- Click **Prompt injection** preset → **Send**.
  → Visualizer lights up **input_scan → BLOCK** (red). *"A jailbreak attempt is
  blocked. In your code this is just a normal `openai.PermissionDeniedError` — catch
  it like any API error."*
- Click **Clean** → normal answer, all-green pipeline. *"Benign traffic is untouched."*

---

### 4 · RAG with grounding (90s)
- **RAG** tab → paste a short policy doc (e.g. *"Enterprise refunds process in 5 days…"*)
  → **Index document**.
- Ask *"How fast are enterprise refunds?"* → grounded answer **"…within 5 days"** with
  the **source** shown.
- Ask something not in the doc → *"No relevant documents / I don't know."*
> "The client owns retrieval; the gateway grounds the answer, scans the retrieved
> context for poisoning, and runs hallucination guardrails on the output."

---

### 5a · MCP context injection (45s)
- **MCP** tab → **Context injection** — JSON shows a customer profile.
- **Run with context** → a customer summary that uses the injected `customer_id`,
  `plan`, `sentiment`.
> "Structured context (CRM, tickets, profiles) is injected and **governed** — the
> gateway scans it for PII/secrets. This is *not* picking an MCP server."

### 5b · MCP tool execution (60s)
- Same tab → **Tool execution** — pick a **connected** server and tool (e.g.
  `everything-1` / `echo`), edit JSON args, **Invoke tool**.
- Point at the result and the live SDK / httpx snippet on the right.
> "Server and tool selection is here — same control API as the console simulator.
> Production apps can also use the official MCP Client against
> `/gateway/{org}/mcp/{server}` with the org gateway key."

---

### 6 · Files (45s)
- **Files** tab → upload a PDF/CSV → **Analyze** → summary + sensitive-data flags.
> "Upload a document; the same firewall + routing apply to the analysis."

---

### Close (30s)
> "Everything you saw — routing, governance, PII/credential/injection blocking,
> RAG grounding, MCP governance — ran through the **unmodified OpenAI SDK**. Two
> lines: `base_url` and `api_key`. Your existing OpenAI app is now governed and
> secured, with a full audit trail in the ZeroShield dashboard."

**Backup if a model is unavailable:** the routing card will show the kill-switch
reroute — use it as the governance talking point rather than a failure.
