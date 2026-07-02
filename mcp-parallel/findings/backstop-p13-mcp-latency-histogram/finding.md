# CHG-0088 — MCP tool-call latency histogram (item-13 monitoring; completes CHG-0087)

**Change-id:** CHG-0088
**Date:** 2026-07-02
**Severity:** LOW–MEDIUM (observability: MCP tool-call latency percentiles were not exposed as a metric)
**Area:** HARDEN THE ARCHITECTURE — Phase-3 monitoring/metrics (item 13) + "1.4 under peak load" observability (item 20)
**Files:** `gateway/ai_mesh_gateway/metrics.py`, `gateway/ai_mesh_gateway/mcp_proxy.py` (+ `tests/test_mcp_scan_metrics.py`)
**Whose work it touches:** the gateway metrics module + the MCP audit sink (`_record_gateway_event`); the documented CHG-0087 residual.

## Gap (CHG-0087 residual)

CHG-0087 added MCP scan-decision + compliance-tag COUNTERS but not a latency metric. `_record_gateway_event`
already carries `latency_ms` (16+ call sites pass a computed `int((time.time()-call_t0)*1000)`), yet MCP
tool-call latency was never exposed to Prometheus — so dashboards/alerting could not see MCP p50/p95/p99,
which is exactly what "1.4 guardrails holding under peak load" (item 20) needs to observe.

## Fix

- `metrics.py`: new `amf_gateway_mcp_call_seconds{org, decision}` Histogram (buckets 5ms…10s).
  `record_mcp_scan_decision(...)` gains a `latency_ms` param and observes `latency_ms/1000` — but ONLY when
  `latency_ms` is truthy, so paths that don't time the call (e.g. the tools/list metadata-scan audit) don't
  skew the low bucket with 0-second samples.
- `mcp_proxy._record_gateway_event`: passes `latency_ms` to `record_mcp_scan_decision` (still best-effort /
  try-except so metrics can never break the request path).

## Behaviour after fix (verified)

- Two block decisions at 125ms + 340ms → `mcp_call_seconds_count{block}` +2, `_sum` = 0.465 (seconds).
- `latency_ms` of 0 / None / omitted → decision counter still moves but **no** latency observation.
- The audit sink (`_record_gateway_event(latency_ms=88)`) records one observation (wired end-to-end).
- No-op without prometheus_client (never raises).

## Verify

```
cd gateway
.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_scan_metrics.py -q   # 6 passed (4 CHG-0087 + 2 latency)
.venv/bin/python -m pytest ai_mesh_gateway/tests -q                            # 1461 passed, 0 failed
```
Broker regression: `services/mcp-broker && .venv/bin/python -m pytest tests -q -k "not websocket"` → 108 passed.

## Residual / follow-ups
- OpenTelemetry tracing (spans for the authz→minimize→scan+redact→tag→audit per-call chain) remains a
  separate item-13 piece.
- A few audit calls (tools/list metadata scan, ext-proxy `_ext_audit`) don't pass `latency_ms` yet — they
  still count decisions/tags; wiring their latency is a minor follow-up.
