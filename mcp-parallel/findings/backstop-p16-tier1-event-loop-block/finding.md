# CHG-0103 — the MCP Tier-1 scan blocked the event loop (severe DoS under load)

**Change-id:** CHG-0103
**Date:** 2026-07-03
**Severity:** HIGH (availability / DoS — a single large result froze every concurrent request on the worker; directly contradicts "1.4 guardrails holding under peak load" + "resource bombs contained").
**Area:** HARDEN item 16 "resource bombs contained" + item 20 "1.4 under peak load" — the scan concurrency architecture.
**Files:** `gateway/ai_mesh_gateway/mcp_scan_orchestrator.py` (`_scan_text_tier1` split into `_scan_text_tier1_sync` + an offloading async wrapper); `gateway/ai_mesh_gateway/tests/test_mcp_tier1_offload.py` (new).
**Whose work it touches:** the owning-session MCP scan core (pre-existing gap — the MCP Tier-1 ran inline, unlike the chat scanner which already offloads via `run_in_executor`).

## Root cause

`mcp_scan_orchestrator._scan_text_tier1` was `async def` but its body is PURE SYNCHRONOUS CPU work — `detect_pii` / `detect_secrets` / `detect_ip_leakage` / `detect_credential_exposure` (each a loop of `re.search` over the text), `redact_all`, the encoded-exfil decode loop, and the render-leak neutralizers — with NO `await` inside. So it ran INLINE on the event loop. A large tool result (up to the 10 MB response cap) is seconds of pure CPU: detect_pii alone is ~2.6 s on 8 MB, the whole Tier-1 ~5–10 s. Because it never yields, it BLOCKS the event loop — freezing EVERY other concurrent request (chat, MCP, health) on that worker.

**Measured (before):** an ~8 MB result scan stalled a trivial `asyncio.sleep(0.05)` coroutine for **9.67 s** (the loop was blocked the entire scan). Under the 5k–10k-concurrent stress scenario, a few large results repeatedly freezing workers is a severe availability failure — and an untrusted upstream can trigger it with one crafted result.

## The fix (CHG-0103)

Split `_scan_text_tier1` into a pure-CPU sync function `_scan_text_tier1_sync` (the unchanged body) and an async wrapper `_scan_text_tier1` (same signature) that offloads to a worker thread via `asyncio.to_thread` when the text exceeds `_TIER1_OFFLOAD_THRESHOLD` (64 KB, env `MCP_TIER1_OFFLOAD_BYTES`); a small input runs inline (sub-ms; offloading it would only add thread-pool pressure). The `re` loop releases the GIL between patterns, so the thread keeps the loop responsive.

**Measured (after):** the same 8 MB scan stalls the trivial coroutine only **~0.2 s** (loop free). The secret in the 8 MB result is still masked (correctness preserved through the thread).

## Verification (all green)

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_tier1_offload.py -q   # 4 passed
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                             # 1609 passed, 0 failed
cd ../services/mcp-broker && ./.venv/bin/python -m pytest tests -q -k "not websocket"  # 108 passed
```

4 new tests (deterministic — patch `to_thread`, no flaky timing): a large result OFFLOADS the Tier-1 sync scan to a thread (and the secret is still masked through the thread); a small result runs INLINE (no offload) and is still masked; the threshold is positive + env-configurable; `_scan_text_tier1_sync` is sync and `_scan_text_tier1` is async. All prior Tier-1 tests (injection parity, scan orchestrator, unicode deobfuscation, credential exposure, concurrency) still pass — the wrapper keeps the async signature so callers/tests are unchanged.

## Scope / honesty note

This is a pre-existing architecture gap (the MCP Tier-1 never offloaded, unlike the chat scanner's `run_in_executor`); my CHG-0096–0100 render-leak neutralizers added to the inline cost but did not create the blocking. Fixing it makes the scan non-blocking under load. RESIDUAL: the total CPU cost of scanning a very large result (~10 s for 9 MB) is unchanged — it just no longer blocks the loop; capping/reducing that cost is a separate product-level trade-off. Proof is the loop-stall measurement (9.67 s → 0.2 s) + the deterministic offload tests (no content leak → no aidefence oracle). Does not change the host-blocked live-stress status (items 14–20). Partial coverage is not completion.
