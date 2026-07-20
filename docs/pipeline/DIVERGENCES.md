# Chat Pipeline Divergence Analysis

> **Generated**: 2026-07-03 | **Source**: `gateway/ai_mesh_gateway/main.py` (13036 lines)
> **Item**: P1-02 — Identify divergences across code paths, fail-open sites, streaming vs non-streaming enforcement
> **Cross-ref**: docs/pipeline/PATHS.md (P1-01)

---

## 1. Block Short-Circuit Divergences

### D-01: Input Block — `enforcement_mode != "block"` continues to LLM (L6548)

| Aspect | Path A/B/C/D (all) |
|--------|---------------------|
| **Behavior** | Scanner verdict = BLOCK, but `enforcement_mode` is not `"block"` (e.g. `"monitor"`) |
| **Code** | L6486: `_should_block_verdict` is True (scanner wants block) → L6513: `if enforcement_mode == "block": return 403` … L6548: `else: LOG.warning("MONITOR: would block …")` — **falls through**, execution continues to LLM call |
| **Severity** | **HIGH (leak by design)** — PII/injection that the scanner flags BLOCK reaches the model when `enforcement_mode="monitor"`. This is by-design ("monitor mode logs but doesn't block"), but operators may not realize it means raw PII egresses to the LLM provider |
| **Evidence** | The "input_scan BLOCK but model_output 7710ms" trace is consistent with this: enforcement_mode could be `"monitor"`, allowing the request through despite BLOCK verdict |
| **Recommendation** | The divergence is architectural/intentional. However, PII/secret redaction should still apply even in monitor mode — a block verdict should trigger redaction (mask the PII) before forwarding to the model, rather than forwarding raw. Currently L6571 `_should_apply_redaction` is `True` for `flag`/`monitor` with PII threat types, but the redaction branch is AFTER the block check, so it runs. **Root issue**: the `_should_hard_block → else → fall-through` path does NOT skip the redaction section — it falls through and redaction CAN run. So this is safe for PII if `_should_apply_redaction` returns True. For injection (non-redactable), the raw prompt reaches the model in monitor mode. |

### D-02: Input Block — Tier-2 Degraded overrides any Tier-1 decision (L6383)

| Aspect | Path A/B/C/D (all) |
|--------|---------------------|
| **Behavior** | `_is_tier2_degraded_verdict(verdict)` returns True → falls through to enforcement resolution with the DEGRADED verdict |
| **Code** | L6383-6433: checks `verdict.tier == "tier_2"` AND (`threat_type == "bedrock_degraded"` OR `reason_code.startswith("degraded")`). If True, logs warning and **falls through**. |
| **Critical detail** | The verdict that reaches enforcement resolution (L6435+) IS the degraded verdict. Its `verdict.action` is whatever the scanner returned for a degraded result (likely `"allow"` or `"monitor"`). Tier-1 ran first inside `scan_prompt_with_tier2` and passed (if Tier-1 had blocked, the scan function would have returned a Tier-1 block verdict, not a Tier-2 degraded one). So this is NOT "Tier-1 blocked but degraded overrides" — it's "Tier-1 passed, Tier-2 couldn't confirm, fall back to Tier-1's clean decision". |
| **Severity** | **MEDIUM** — Tier-1 regex-only coverage has known gaps (semantic PII, subtle injection, obfuscated payloads). A request that Tier-1 passes but Tier-2 would have caught egresses raw. Not a bug per se (designed fail-open for availability), but a security gap when Bedrock is flaky. |
| **Recommendation** | Option A: degrade to REDACT (mask all detected PII patterns) instead of ALLOW; option B: `tier2_strict=True` already exists (returns 503 when breaker OPEN) — extend strict to per-request degraded too. |

### D-03: Standalone Block Return vs Connected Block Return — identical

All four paths share the same enforcement resolution code (L6435-6691). Block returns are identical: L6528 `return _build_block_response(403, …)` and L6669 `return _build_block_response(403, …)` for unmaskable PII. No divergence here — this is correct.

---

## 2. Streaming vs Non-Streaming Enforcement Matrix

### D-04: Output Guard Mechanism

| Behavior | Non-Streaming (Path B/D) | Streaming (Path A/C) |
|----------|--------------------------|----------------------|
| **Guard location** | Path B: `_apply_output_guard_nonstream` (L6877) / Path D: inline `OUTPUT_GUARD.inspect` (L7589) | `SecureStreamingResponse._flush_buffer` (secure_streaming.py:318) |
| **Scan input** | FULL response text (complete LLM output) at once | Buffered chunks (~4KB or at sentence boundaries) — scans partial text |
| **Block action** | Returns HTTP 403 JSONResponse (Path D: L7683 / Path B: L1736) | Emits SSE error frame + `[DONE]` (secure_streaming.py:397-401); HTTP status already 200 |
| **Redact action** | Mutates `llm_resp` dict in-place (L7708-7716); returns modified JSON | Yields rebuilt SSE chunks with redacted deltas (secure_streaming.py:464) |
| **Rewrite action** | Re-infers via LLM router + re-validates once (L7779-7815) | Coerced to BLOCK — no mid-stream re-inference analog (secure_streaming.py:379) |
| **Flag action** | Metadata-only (zeroshield `action: "flag"`), response unchanged | Same: metadata-only, original chunks released |
| **Degraded scan** | M11: emits `output_scan_degraded` telemetry, response ships unscanned (L7599-7613) | H-03: emits same telemetry once per stream (secure_streaming.py:366-368), response ships unscanned |
| **Fail-open on exception** | `_apply_output_guard_nonstream`: returns None → response ships (L1694-1696) | `SecureStreamingResponse.__aiter__` except: clears buffers, emits error SSE, does NOT release unscanned content → **fail-closed** (secure_streaming.py:299-316) |
| **Unmaskable PII honesty** | Non-stream main.py inline: checks `_redact_changed` → if no-op reports "flag" not phantom "redact" (L7714-7715) | Streaming: same check (secure_streaming.py:443-444) |

### D-05: **DIVERGENCE — Non-stream output guard fails OPEN, streaming fails CLOSED**

| Path | Fail behavior on guard exception |
|------|----------------------------------|
| **Path B** (`_apply_output_guard_nonstream` L1694) | `except Exception: return None` — response ships to client UNSCANNED |
| **Path D** (inline L7589) | `output_verdict = await OUTPUT_GUARD.inspect(…)` — if this raises, it propagates up the handler and becomes HTTP 500 (safe: does not forward the response) |
| **Path A/C** (streaming, secure_streaming.py:299-316) | `except Exception: self._clear_buffers()` + emits error SSE + `[DONE]` — **fail-closed**: unscanned buffered content is NOT released |

**Severity**: **HIGH** — Path B (standalone sync non-streaming) fails OPEN on an output guard exception: the full model response (potentially containing PII/secrets) ships to the client with zero output scanning. Path D would 500 (safe). Streaming is safe (clears buffers).

**Recommendation**: `_apply_output_guard_nonstream` should fail CLOSED: on exception, either block (return 403) or redact the response defensively, not ship it raw.

### D-06: Output Guard Scan Text Source

| Path | What gets scanned |
|------|-------------------|
| **Path B** (`_apply_output_guard_nonstream` L1676) | `_extract_scannable_output_text(resp)` — content + reasoning_content + tool_calls |
| **Path D** (inline L7554) | Same: `_extract_scannable_output_text(llm_resp)` |
| **Path A/C** (streaming, secure_streaming.py:323) | `"".join(self._content_buffer)` — accumulated content deltas only; `_extract_content_delta` extracts `choices[0].delta.content` |

**Divergence**: Streaming scans ONLY `content` deltas. `reasoning_content` and `tool_calls` emitted in SSE chunks are NOT accumulated into the content buffer → they pass through unscanned (secure_streaming.py:275-276: non-content chunks are `yield raw_sse; continue`).

**Severity**: **MEDIUM** — A secret/PII in `reasoning_content` or a `tool_calls` argument in a streamed response bypasses the output guard entirely. The non-stream F4 fix (L7554) specifically added reasoning_content + tool_calls scanning.

**Recommendation**: `_extract_content_delta` should also extract `reasoning_content` and `tool_call` function arguments for scanning, or a secondary scan buffer should accumulate them.

---

## 3. Degraded / Fail-Open Sites

### D-07: Input Scan Degraded — raw prompt to LLM

| Site | L6383-6433 |
|------|-----------|
| **Condition** | Tier-2 unavailable (bedrock_degraded / client_error / parse_failure) |
| **Behavior** | Falls through with Tier-1-only verdict. Enforcement resolution runs on the degraded verdict (which is typically `action="allow"` or `"monitor"`, NOT `"block"`). |
| **Does block stop model call?** | If Tier-1 already passed and Tier-2 is degraded, the degraded verdict falls through to enforcement → `_resolve_enforcement(allow, …)` → allow → **model IS called with raw prompt**. Block only fires if Tier-1 itself blocks (in which case the verdict is NOT a degraded one). |
| **Evidence link** | The known "input_scan BLOCK + model_output 7710ms" trace: if Tier-1 blocked (action=block, tier=tier_1) AND the model was called, then block did NOT short-circuit. However, per the code, `enforcement_mode != "block"` would cause the block to log-only (D-01 above). OR: the block telemetry was emitted but the 403 return was NOT executed because `enforcement_mode` was not "block". |

### D-08: Output Scan Degraded — response ships unscanned

| Site | L7560 (Path D) / secure_streaming.py:366 (Path A/C) / L1694 (Path B) |
|------|------|
| **Condition** | `verdict.scan_degraded = True` (Tier-2 output guard model unavailable) |
| **Behavior** | Response is delivered to the client WITHOUT confident output scanning. `output_scan_degraded` telemetry emitted. |
| **Severity** | **MEDIUM** — model output containing PII/secrets/IP passes through to the client unscanned. Surfaced in zeroshield metadata so operators CAN detect it. |

### D-09: `_apply_output_guard_nonstream` exception → fail-open (Path B only)

| Site | L1694-1696 |
|------|-----------|
| **Condition** | Any exception from `OUTPUT_GUARD.inspect()` |
| **Behavior** | Returns `None` → caller ships the response as-is. LOG.exception but no telemetry/audit for the failure. |
| **Severity** | **HIGH** — a transient guard error (OOM, timeout, bad model response) causes the response to ship with zero output scanning. No `output_scan_degraded` flag is set (the degraded flag comes from the verdict, which doesn't exist on exception). |
| **Recommendation** | Fail CLOSED: on exception, return a block response (or at minimum, apply `redact_all` defensively and emit `output_scan_degraded` telemetry). |

### D-10: RAG Context Binding — fail-open (L1644 + L7587)

| Site | Both output guard paths |
|------|------------------------|
| **Condition** | Redis lookup for RAG context fails |
| **Behavior** | `except Exception: pass` — context_chunks stays empty. The output guard runs WITHOUT grounding context. |
| **Severity** | **LOW** — the guard scores grounding=1.0 with no context (safe branch), so hallucination verdicts are suppressed. Not a PII/secret leak risk. |

---

## 4. One Path Short-Circuits, Another Continues

### D-11: Policy Block — short-circuits identically everywhere

The policy-first block (L6000-6247) runs BEFORE the 4-path branch point. A policy `action="block"` at L6073-6093 returns `_build_block_response(403, …)` → short-circuits. All paths see this identically. No divergence.

### D-12: Kill-switch — short-circuits identically everywhere

Kill-switch checks (L4760) run in the shared pre-scan pipeline. All paths see this identically. No divergence.

### D-13: Blocked keywords — short-circuits identically everywhere

Blocked keywords (L5869) run in the shared pre-scan pipeline. All paths see this identically. No divergence.

### D-14: Input block — enforcement_mode "block" vs non-"block"

Already covered in D-01. The SHORT-CIRCUIT (return 403) only fires when `enforcement_mode == "block"`. All other modes log-and-continue. This applies to ALL 4 sub-paths identically (they share the same code). Not a path divergence, but a mode divergence.

### D-15: Output block — Path D (inline) vs Path B (`_apply_output_guard_nonstream`)

Both block on `verdict.action == "block"`: Path D returns `_build_block_response` (L7683), Path B returns `_build_block_response` (L1736). Behavior identical.

Streaming: emits SSE error + [DONE], stops stream. Different mechanism, same enforcement outcome (content never delivered).

### D-16: Unmaskable PII — consistent across paths

The L6615-6691 unmaskable PII fail-closed check runs in the shared enforcement code before the path branch. All paths see it identically.

---

## 5. Streaming-Specific Divergences

### D-17: Streaming — already-emitted content cannot be blocked retroactively

| Aspect | Detail |
|--------|--------|
| **Behavior** | `SecureStreamingResponse` buffers ~4KB (sentence boundaries) before scanning. When a block verdict arrives at flush, PREVIOUSLY EMITTED chunks are already at the client. Only the CURRENT buffer is withheld. |
| **Severity** | **MEDIUM** — a long PII value that spans multiple flush boundaries could have its first half delivered before the second half triggers a block. The E14 lookahead / secret-anchor mechanisms mitigate this for secrets, but not for arbitrary PII. |
| **Non-stream contrast** | Non-stream scans the COMPLETE response before ANY delivery. Perfect retroactive blocking. |

### D-18: Streaming — flag-then-block escalation in `enforcement_mode=block`

| Aspect | Detail |
|--------|--------|
| **Code** | secure_streaming.py:371-372: `if verdict.action == "flag" and self._enforcement_mode == "block": effective_action = "block"` |
| **Behavior** | A "flag" verdict (below block threshold) is ESCALATED to "block" when the org enforcement mode is "block". |
| **Non-stream contrast** | The non-stream path (L7630+) does NOT escalate "flag" to "block" — a flag verdict on Path D stays "flag" (metadata-only). This is an INCONSISTENCY between streaming and non-streaming enforcement. |
| **Severity** | **LOW** — streaming is MORE aggressive (blocks on flag + block mode), non-streaming is LESS aggressive (allows on flag). Net security is that streaming is stricter. But it means the same request can produce different enforcement outcomes depending on `stream=true/false`. |

### D-19: Streaming — "rewrite" coerced to "block"

| Aspect | Detail |
|--------|--------|
| **Code** | secure_streaming.py:379-380: `if verdict.action == "rewrite": effective_action = "block"` |
| **Non-stream** | Path D L7777-7815: re-infers a safe response via LLM router, validates, falls back to canned safe message. |
| **Behavior** | Streaming: client sees an error SSE + [DONE] (stream aborted). Non-streaming: client sees a rewritten (safe) response. |
| **Severity** | **LOW** — this is a known limitation (no mid-stream re-inference), documented in the code. Not a leak, but a UX asymmetry. |

---

## 6. Cross-Reference: The Known Leak (input_scan BLOCK + model_output 7710ms)

Based on the divergence analysis, the known leak (`input_scan` event_type=`input_blocked` action=`block` coexisting with a 7710ms `model_output`) has these candidate root causes:

### Hypothesis A: enforcement_mode != "block" (D-01) — **MOST LIKELY**

- Scanner emits a BLOCK verdict → `_should_hard_block` is True
- L6486-6512: `input_blocked` telemetry is emitted with `action="block"` even when `enforcement_mode != "block"` (L6498: `action="block" if enforcement_mode == "block" else "monitor"` — wait, this IS conditional)
- Actually re-reading L6498: `action="block" if enforcement_mode == "block" else "monitor"` — so the telemetry says "monitor" not "block" in monitor mode. This may NOT match the evidence.

### Hypothesis B: Double telemetry (block emitted, then fall-through)

- Re-reading L6486-6552: the block TELEMETRY is emitted at L6491 BEFORE the `if enforcement_mode == "block":` check at L6513. So the `input_blocked` event is ALWAYS emitted when `_should_hard_block` is True, regardless of enforcement_mode.
- But: L6498 sets `action="block" if enforcement_mode == "block" else "monitor"`. If the evidence shows `action="block"`, enforcement_mode was "block", and the 403 return at L6528 SHOULD have fired — meaning the model call SHOULD NOT have happened.
- Unless: there's a bug where the 403 return doesn't actually stop execution? In async Python, `return` from an `async def` route handler does terminate the handler — so this seems unlikely.

### Hypothesis C: Race condition / concurrent request

- The `input_blocked` telemetry belongs to request A (which WAS blocked at 403), and the `model_output` telemetry belongs to request B (which was allowed). If `request_id` differs, this is a red herring (event conflation, not a leak).

### Hypothesis D: The block is from POLICY (not scanner)

- The policy-first block at L6073 emits `input_blocked` telemetry AND returns 403. If the policy block returns 403, the scanner never runs. But if the scanner ALSO emits `input_blocked` for a separate detection, and the model call happened between the two events (which can't happen — they're sequential in the same handler), this is impossible.

### Recommendation for Item 02

The most productive next step is to match the specific `request_id` of the `input_blocked` event against the `model_output` event. If they share the same `request_id`, there IS a leak. If different request_ids, it's event conflation (P6 item 23). The code-level divergences do NOT show a path where `enforcement_mode="block"` + scanner BLOCK verdict + `return _build_block_response(403, …)` can be bypassed — UNLESS the telemetry evidence was misread (action was "monitor", not "block").

---

## 7. Summary: Highest-Risk Divergences

| # | Divergence | Severity | Leak? | Fix Complexity |
|---|-----------|----------|-------|----------------|
| D-05 | `_apply_output_guard_nonstream` fails OPEN on exception (Path B) | **HIGH** | Yes — model output ships unscanned | LOW (change return None to return block response) |
| D-06 | Streaming output guard does NOT scan reasoning_content / tool_calls | **MEDIUM** | Yes — PII in non-content channels bypasses streaming guard | MEDIUM (extend `_extract_content_delta`) |
| D-02 | Tier-2 degraded → Tier-1-only (fail-open for availability) | **MEDIUM** | Conditional — only if Tier-1 misses a threat Tier-2 would catch | HIGH (availability vs security trade-off) |
| D-08 | Output scan degraded → response ships unscanned | **MEDIUM** | Yes — model output with PII ships to client | MEDIUM (fail-closed = defensive redact) |
| D-17 | Streaming already-emitted content cannot be retroactively blocked | **MEDIUM** | Partial — first chunks may egress before block | MEDIUM (larger buffer, but latency trade-off) |
| D-18 | Streaming escalates flag→block in block mode, non-streaming does not | **LOW** | No — streaming is stricter | LOW (harmonize) |
| D-09 | No telemetry/audit for output guard exception (Path B) | **MEDIUM** | No PII leak, but invisible failure | LOW (emit telemetry) |
| D-01 | Monitor mode: block verdict → log-only, model called | **By design** | Yes for injection (raw egress); PII is redacted | N/A (policy decision) |

---

## 8. File References

| File | Lines | Role |
|------|-------|------|
| `gateway/ai_mesh_gateway/main.py` | 4509-8900 | `proxy_chat` handler (all 4 sub-paths) |
| `gateway/ai_mesh_gateway/main.py` | 1648-1760 | `_apply_output_guard_nonstream` (Path B output guard) |
| `gateway/ai_mesh_gateway/main.py` | 2683-2855 | `_launch_chat_stream_response` (Path A/C streaming launcher) |
| `gateway/ai_mesh_gateway/main.py` | 3529-3536 | `_is_tier2_degraded_verdict` |
| `gateway/ai_mesh_gateway/main.py` | 6383-6433 | Tier-2 degraded fall-through |
| `gateway/ai_mesh_gateway/main.py` | 6435-6691 | Enforcement resolution + block/redact |
| `gateway/ai_mesh_gateway/main.py` | 7556-7860 | Path D inline output guard |
| `gateway/ai_mesh_gateway/enforcement.py` | 1-147 | `resolve_enforcement`, `should_hard_block`, `should_apply_redaction` |
| `gateway/ai_mesh_gateway/secure_streaming.py` | 129-570 | `SecureStreamingResponse` (streaming output guard) |
