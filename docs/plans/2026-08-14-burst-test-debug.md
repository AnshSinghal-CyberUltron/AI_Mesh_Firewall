# Attack Simulator Burst Test — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make Burst Test honest and useful: the UI must send the concurrency/count the operator set (or refuse it), skip-inference must actually skip the LLM, errors must show HTTP status/code/message, and Rate-Limit Probe must be able to produce a real 429.

**Architecture:** Burst is a browser worker-pool of live `POST /v1/chat/completions`. The gateway currently always infers after input scan. Fix in three layers: (1) UI honesty + diagnostics, (2) a real scan-only server contract that returns before `acompletion`, (3) only then raise caps for scan-only bursts. Do not raise 100/25 while each request still bills a model.

**Tech Stack:** React (`AttackSimulatorPanel.jsx`, `liveGateway.js`), FastAPI gateway (`main.py`, `llm_router.py`), nginx, Redis, LiteLLM.

---

## What we proved on prod (2026-08-14 04:25 UTC)

| Operator set | What actually ran |
|---|---|
| Requests 500 | **100** (`Math.min(100, …)`) |
| Concurrency 100 | **25** (`Math.min(25, …)`) |
| Estimated tokens 8000 | **not sent** (Standard Burst → UI shows "auto") |
| "Burst skips inference" | **false** — every request called LiteLLM |
| Sequential processing | **false** — 25 in-flight at `04:25:38Z`; 27764ms ≈ 4 waves × ~7s |
| 91 REDACT | Sensitive Data Leakage prompt (SSN/email/CC) — redact-and-forward, then LLM |
| 9 ERROR | HTTP **503** LiteLLM cooldown after OpenRouter **404** on retired `hermes-3-llama-3.1-405b:free` |

Nginx in that minute: **92×200 + 9×503 + 1×401**. Gateway was up. Earlier 502s (04:21) were a **separate** nginx stale-DNS incident (`172.18.0.12`).

---

## Root causes (ranked)

1. **Silent UI clamp** — inputs accept 500/100; send path caps 100/25. Button already shows `×100`.
2. **Skip-inference is a lie** — `runInference:false` omits `max_tokens`; gateway injects `max_response_tokens` then always `LLM_ROUTER.acompletion`.
3. **Burst ERROR rows hide status/message/code** — capture has `status`; render shows only ERROR + ms + `zs-*`.
4. **Results listed by start index** — looks sequential even when concurrent.
5. **Retired free Hermes + empty LiteLLM fallback on 503** — 9 overlapping requests failed during 5s cooldown; 9th opened the circuit; later requests silent-rerouted to Gemini.
6. **Rate-Limit Probe `estimated_tokens` is ignored** by `_estimate_request_tokens`; 12×8000 also cannot trip the 100k simulator TPM even if wired.
7. **Redis hot-path pool uncapped** — one of the 9 also logged `Too many connections` (fail-closed model-state).

---

## Non-goals / safety

- Do **not** raise burst caps to 500/100 until scan-only is byte-proven (no `acompletion` in gateway logs).
- Do **not** send PII prompts on Rate-Limit Probe.
- Do **not** treat CB/upstream 503 as "rate limited."

---

### Task 1: Honest burst controls (frontend)

**Files:**
- Modify: `frontend/src/components/AttackSimulatorPanel.jsx`
- Test: `frontend/src/utils/liveGateway.test.js` or a new `AttackSimulatorPanel` burst helper test if one exists; otherwise extract clamp helpers and unit-test them.

**Step 1: Extract clamp helpers**

```javascript
export const BURST_MAX_REQUESTS = 100;
export const BURST_MAX_CONCURRENCY = 25;

export function clampBurstCount(raw) {
  return Math.min(BURST_MAX_REQUESTS, Math.max(1, Number(raw) || 10));
}
export function clampBurstConcurrency(raw, requestCount) {
  return Math.min(BURST_MAX_CONCURRENCY, Math.max(1, Number(raw) || requestCount));
}
```

**Step 2: Clamp on change + helper text**

- `onChange` writes the clamped number, not the raw string.
- Under the inputs: `Gateway will send {n} requests at {c} in-flight (max {MAX}/{MAXC}).`
- If the operator types above max, show a warning chip, do not silently keep 500 in the box.

**Step 3: Results header must show requested vs sent**

`Requested 500/100 → sent 100/25` until Task 6 raises caps.

**Step 4: Commit**

```bash
git add frontend/src/components/AttackSimulatorPanel.jsx
git commit -m "fix(burst): clamp request/concurrency in the UI, not only at send"
```

---

### Task 2: Burst ERROR diagnostics

**Files:**
- Modify: `frontend/src/components/AttackSimulatorPanel.jsx`
- Modify: `frontend/src/utils/liveGateway.js` (`inferBlockedStage` for `circuit_breaker_open` / `model_isolated`)

**Step 1:** Store `http_status`, `code`, `message` from `res.data` in `runOne`. Catch path: keep `err.message`, `status: 0`.

**Step 2:** Render ERROR rows as `ERROR · HTTP 503 · upstream_error · The upstream inference service is unavailable.` plus `zs-*`.

**Step 3:** Do not show "No rate limiting observed. Increase requests…" as the primary banner when `errors > 0`. Show error count first.

**Step 4:** Commit.

---

### Task 3: Completion-order + in-flight meter

**Files:**
- Modify: `frontend/src/components/AttackSimulatorPanel.jsx`

**Step 1:** Record `started_at` / `finished_at` per row. Add a toggle: "Order by start index" (default, current) vs "Order by completion."

**Step 2:** While running, show `in flight: k / concurrency` so 25-wide is visible.

**Step 3:** Commit.

---

### Task 4: Real scan-only on the gateway (root cause of 4–9s + 503s)

**Files:**
- Modify: `gateway/ai_mesh_gateway/main.py`
- Test: `gateway/ai_mesh_gateway/tests/` new `test_scan_only_skips_acompletion.py`

**Contract:** If `max_tokens` is absent or `0` **and** `stream` is false, after input scan/redact/block:

- BLOCK → existing 403 (no LLM)
- REDACT/ALLOW → return 200 with scan verdict, **do not** inject `max_response_tokens`, **do not** call `LLM_ROUTER.acompletion`, **do not** run circuit-breaker / kill-switch inference path

**Step 1: Failing test** — mock `LLM_ROUTER.acompletion`; POST `{model, messages}` with no `max_tokens`; assert `acompletion` call_count == 0 and JSON has `zeroshield.action` from input scan.

**Step 2: Implement** — skip the inject `else: body["max_tokens"] = max_tokens_config` when this is a scan-only probe; return after input enforcement.

**Step 3:** Byte-verify: gateway logs for a burst of 10 have **zero** `litellm.acompletion` lines.

**Step 4:** Update UI copy: "Burst is scan-only (no model call) unless you enable Inference burst."

**Step 5:** Commit.

---

### Task 5: Rate-Limit Probe actually 429s

**Files:**
- Modify: `gateway/ai_mesh_gateway/main.py` (`_estimate_request_tokens` / TPM pre-charge)
- Modify: `frontend/src/components/AttackSimulatorPanel.jsx`

**Step 1:** Honor `body["estimated_tokens"]` for TPM **only** on authenticated simulator/admin keys, cap it (e.g. 1..1_000_000), strip the key before LiteLLM.

**Step 2:** Rate-Limit Probe **forces a clean prompt** (ignore scenario PII/jailbreak).

**Step 3:** Default probe math must exceed simulator TPM (100_000): e.g. 20 req × 8000 = 160k, or a dedicated low TPM probe key. Document which limiter is under test (key TPM vs org burst/RPM).

**Step 4:** Commit.

---

### Task 6: Raise caps only for scan-only

**Files:**
- Modify: `frontend/src/components/AttackSimulatorPanel.jsx`

**After Task 4 is proven:** raise scan-only max to 500 requests / 100 concurrency. Keep a separate **Inference burst** cap at 25/10.

**Do not ship this task before Task 4 evidence.**

---

### Task 7: Prod model + Redis hygiene (this incident's 9 errors)

**Ops (no code required to explain the 9 errors):**
- OpenRouter retired `nousresearch/hermes-3-llama-3.1-405b:free`. Disable/replace that org model; keep `gemini-flash-cheap` (or another live slug) as fallback.
- Confirm Model State fallback is set so CB trip reroutes instead of disable.

**Code (follow-up, can be a later PR):**
- Cap hot-path `REDIS_CLIENT` (PERF-0007 left it unbounded).
- Org-scope `circuit:state:{model}` (today global — cross-tenant).
- nginx `/v1/` should use the same `resolver 127.0.0.11` + variable `proxy_pass` as `/demo/` so gateway recreate does not 502 to a stale IP.

---

### Task 8: Abort / timeout / visibility

**Files:**
- Modify: `frontend/src/hooks/useSimulatorEngine.js`, `AttackSimulatorPanel.jsx`

AbortController on Reset/unmount/new burst; per-request timeout; pause or warn if `document.hidden`. Catch must not look like a gateway 503.

---

## Live verification (mandatory)

1. Prod/staging Attack Simulator, Standard Burst, Requests=500 Concurrency=100:
   - Input clamps or clearly sends 100/25 until Task 6.
   - After Task 6 + scan-only: 500 POSTs, ~100 in-flight, **zero** `litellm.acompletion` in gateway logs.
2. Sensitive Data Leakage + scan-only: REDACT (or BLOCK), no LLM.
3. Rate-Limit Probe with clean prompt: at least one HTTP 429 counted as Rate Limited.
4. Kill selected model (retired slug): ERROR row shows **HTTP 503 + code + message**, not a bare ERROR.
5. Repeat 3×; refresh; second tab; background tab warning.
6. Confirm nginx `/gw-health` 200 after a gateway recreate (Task 7 DNS).

---

## Execution choice

Confirm this plan, then pick:

1. **This session** — implement Task 1→4 first (honesty + real scan-only).
2. **Ops-only now** — disable retired Hermes free slug on prod; leave code for a follow-up.
