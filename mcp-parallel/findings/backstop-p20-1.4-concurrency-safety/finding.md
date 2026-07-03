# CHG-0090 — 1.4 scan/redact guardrails proven concurrency-safe (item-20 concurrency dimension) + regression lock

**Change-id:** CHG-0090
**Date:** 2026-07-02
**Severity:** N/A (verification + regression-lock; zero defects found — the guardrail holds)
**Area:** HARDEN 1.4 / item 20 "1.4 guardrails (redaction + …) holding under peak load" — the CONCURRENCY dimension
**Files:** `gateway/ai_mesh_gateway/tests/test_mcp_scan_concurrency_safety.py` (new)
**Whose work it touches:** none (test-only); backstops the shared scan core (patterns.py, mcp_scan_orchestrator.py) against concurrency regressions.

## Why (which part of the mandate)

Item 20 requires the 1.4 guardrails to hold under peak load. The 300–500-sandbox × 5k–10k-concurrent-call
scale is host-blocked on this shared VM, BUT the guardrails' concurrency-SAFETY is provable here: the MCP
scan/redact chain (`_scan_tool_result_floor` → `mcp_scan_orchestrator` → `patterns.redact_all`) runs
against MODULE-LEVEL state (the compiled-pattern LRU cache, the enabled-tools / server-config caches). If
any of that were mutated per-scan or shared across coroutines without isolation, a race could
CROSS-CONTAMINATE concurrent scans — one request's secret/PII leaking into another request's redacted
result, or a canary surviving because a neighbour's scan clobbered shared state. That is a plausible,
high-severity 1.4 failure mode under concurrency that unit tests of a single call cannot catch.

## What it does

`test_mcp_scan_concurrency_safety.py` fires **300 concurrent** `_scan_tool_result_floor` calls
(`asyncio.gather`), each carrying a UNIQUE canary secret (`sk-ant-CANARY####…`) + PII (`user####@…`) +
internal IP, across 10 orgs, and asserts:
1. every call's OWN canary is masked (the guardrail holds — 0 own-canary leaks), and
2. NO call's result contains ANY OTHER call's canary (0 cross-contamination),
plus a 100-way benign concurrent run that must pass through unchanged (no false-block/corruption under
concurrency).

## Result

**0 own-canary leaks, 0 cross-contamination** across 300 concurrent scans → the scan/redact chain is
effectively stateless / isolation-safe under concurrency (patterns are read-only, `redact_all` is pure,
the caches are read-only during a scan). This is the item-20 "1.4 under load" guardrail proven at
unit-concurrency scale, and a durable regression backstop: if a future edit introduces shared mutable state
into the hot scan path, this test flips red.

## Verify

```
cd gateway
.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_scan_concurrency_safety.py -q   # 2 passed
.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                        # 1527 passed, 0 failed
```
Broker regression: `services/mcp-broker && .venv/bin/python -m pytest tests -q -k "not websocket"` → 108 passed.

## Scope / honesty note
This proves concurrency-SAFETY of the guardrails (no cross-contamination race), NOT the full
300–500-sandbox / 5k–10k-concurrent-call live stress (items 14–20), which remains host-blocked and is owned
by the live load harnesses (CP47–50 / mcp_pipeline_matrix_live.py). It is the dimension of item 20 that IS
runnable + verifiable on this VM.
