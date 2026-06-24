# OpenAI-SDK Compat — Production Readiness Assessment (Phase 7)

Validates the Phase-4 fixes (integration branch `fix/oai-phase4-integration` @ `96a1928`) against a
**LIVE uvicorn server** (`http://127.0.0.1:8399`, real HTTP socket + real SSE, **not** ASGITransport)
running the fixed code with the stubbed upstream/auth backend — driven by **diverse stock clients,
base_url+key only**.

> ⚠️ **Scope honesty:** this is the local *fixed-code* live server. The deployed prod gateway
> (`aimeshfirewall.zeroshield.ai`) still runs the **old** code — the fixes are **not deployed**.
> True prod sign-off requires deploying the integration branch behind the real nginx, then re-running
> this matrix against prod. See "Outstanding gates".

## Cross-SDK / cross-framework matrix (× surfaces × verdict, with request_id evidence)

| Client | chat (ns/stream) | responses (ns/stream) | embeddings | models | block (D-a) | auth | request_id |
|---|---|---|---|---|---|---|---|
| **Python `openai` 2.38.0** | ✅ / ✅ | ✅ / ✅ typed | ✅ | ✅ | ✅ `BadRequestError 400` `content_filter` | ✅ 401 | `e.request_id` populated (`zs-…` / `resp_…`) |
| **JS/Node `openai` 6.44.0** | ✅ / ✅ | ✅ / ✅ typed | ✅ | ✅ | ✅ `BadRequestError 400` `content_filter` | ✅ `AuthenticationError` | `e.requestID` populated (camelCase in JS v6) |
| **curl golden transcripts** | ✅ / ✅ (5 `data:` + 1 `[DONE]`) | ✅ / ✅ (10 typed `event:`) | ✅ | ✅ | ✅ 400 nested + `x-request-id` | ✅ 401 | header `x-request-id` == body `request_id` (verified `zs-0712…`==`zs-0712…`) |
| **LangChain `ChatOpenAI`** | ✅ invoke / ✅ stream | — | — | — | — | — | — |
| **Agent-runner (responses + tool)** | — | ✅ end-to-end `function_call` (`get_weather`) | — | — | — | — | — |

- **In-process matrix:** `105 passed, 7 xfailed, 4 xpassed, 0 failed` (the 7 xfail = intentional/needs-live).
- **Golden transcripts:** `docs/contracts/openai_sdk_golden/` (8 files: chat/responses ns+stream, embeddings, models, block-400, auth-401).
- **The JS-only "request_id" miss was a TEST artifact** (JS v6 exposes `e.requestID`, not Python's `e.request_id`) — debunked by curl + the JS error-object dump; the gateway is correct on the wire (no header-casing / SSE-flush bug).

## Enforcement — demonstrably unchanged
- Prompt-injection still **hard-blocks** (no inference; Python+JS+curl all get `400 content_filter`).
- Block body is **scrubbed** (curl: generic `"Request blocked due to security policy"` + scrubbed `pipeline_trace`; no raw prompt/secret/detector internals).
- D-a status change is **never 409/429/5xx** (non-retried; verified).
- AIDefence meta-check (Phase 5) flagged injection (critical) + PII; the firewall's own `InputScanner` is authoritative for credentials.

## Gates
1. **Phase-6 adversarial clearance — ✅ CLEARED.** The 5 verifiers (`w1r6ut364`) ran in-process + live.
   v3-regression / v4-state-divergence / v5-stream-breaker = **robust** (0 holes: enforcement unchanged —
   `scanner.py`/`patterns.py`/`pii_detector.py` have **zero** diff, injection hard-blocks with all W3 params at
   0 inference, PII/creds redacted, block body no-leak; request_id header==body==log on every surface incl.
   inbound-collision; stream disconnect/concurrency/ordering/tool_calls all solid). v1+v2 found **3 holes**
   (2 root causes) → **fixed (`482d123`)** + 3 regression tests (`test_p2_phase6_holes.py`): (A) escaped `/v1`
   exceptions (embeddings/responses-store) bypassed the shim → flat 500 / no `x-request-id` — root-cause fix in
   the `@app.exception_handler(Exception)` catch-all (nested envelope + `x-request-id`, closes the whole class);
   (B) responses-stream `zeroshield.action='error'` → `response.failed`, not a false `response.completed`.
   **Matrix `108 passed / 0 failed` on 3 consecutive in-process runs + live-uvicorn** (happy-path green + Fix-A
   proven on the wire: `__raise500__` → 500 nested `gateway_internal_error` + `x-request-id=zs-emb-…`).
2. **Production deployment — ⏳ REMAINING (the only open gate).** Deploy `fix/oai-phase4-integration` @ `482d123`
   behind the real nginx, then re-run this matrix against prod (which still runs the **old** code). Coordinate
   with the parallel session (overlapping work on `fix/openai-compat-phase4`). This is an operational/coordination
   decision, not a code gap.

## Verdict
**Cross-SDK / cross-framework OpenAI-compatibility is DEMONSTRATED** with stock clients (Python 2.38, JS 6.44,
LangChain, agent-runner, curl) using **base_url+key only**, **in-process AND live-uvicorn**, **enforcement
demonstrably unchanged**, and the **adversarial gauntlet cleared** (3 holes found → root-cause-fixed → re-proven).
The fixes are **production-READY**; the only thing between here and production-IS is the **deploy** (gate 2),
which the operator owns.
