# CHG-0089 — MCP per-org rate-limit 429s were unmetered (Prometheus backpressure blind-spot)

**Change-id:** CHG-0089
**Date:** 2026-07-02
**Severity:** MEDIUM (observability: MCP throttling/backpressure invisible to metrics — matters under the "5k–10k concurrent tool calls" stress)
**Area:** HARDEN THE ARCHITECTURE — Phase-3 monitoring/metrics (item 13) + "1.4 under peak load" (item 20)
**Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (+ `tests/test_mcp_ratelimit_metric.py`)
**Whose work it touches:** the MCP per-org rate limiter (CHG-0031/0032 lineage) + the metrics layer (CHG-0087/0088).

## How it was found (metrics-coverage sweep after CHG-0087/0088)

`metrics.record_rate_limit` is called ONLY from the chat handler (`main.py`, tied to the per-MODEL limiter
`check_model_rate_limit`). The per-ORG TPM/burst/RPM limiter `_enforce_org_tpm_rate_limit` /
`_enforce_org_burst_rpm` does NOT meter internally, and the MCP path (`_mcp_org_rate_limit_raw`) that uses
it returned a plain 429 with no metric.

## Gap

Under load, MCP tool calls exceeding the per-org TPM/burst/RPM ceiling get a 429 (CHG-0031/0032), but that
throttling was recorded NOWHERE in Prometheus — only the CHAT per-model limiter was metered. So an MCP 429
storm (exactly what happens under the mandate's 5k–10k concurrent tool calls) was invisible to
dashboards/alerting: operators could not see MCP backpressure, per-org throttle rates, or correlate 429s
with latency.

## Fix

`_mcp_org_rate_limit_raw` now, when it returns a 429 (from either the TPM or the burst/RPM check),
increments the MCP decisions metric via `record_mcp_scan_decision(org, "rate_limited")` — so MCP throttling
lands in the same `amf_gateway_mcp_scan_decisions_total{org, decision}` series as block/redact/allow/monitor
(one MCP-decisions dashboard). Best-effort try/except (metrics never break the request path). The TPM→burst
**short-circuit** (burst not evaluated when TPM already tripped) and the allow (None) path are preserved by
the refactor.

## Behaviour after fix (verified)

- TPM trips → 429 returned, burst NOT called (short-circuit), `mcp_scan_decisions_total{decision=rate_limited}` +1.
- Burst trips (TPM clear) → 429, metered +1.
- Allowed (both clear) → None, NO metering.
- `auth_ctx is None` → None (no-op, unchanged).

## Verify

```
cd gateway
.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_ratelimit_metric.py -q   # 4 passed
.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_rate_limit.py -q          # 14 passed (behavior intact)
.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                 # 1501 passed, 0 failed
```
Broker regression: `services/mcp-broker && .venv/bin/python -m pytest tests -q -k "not websocket"` → 108 passed.

## Residual / follow-ups
- The per-org limiter (`_enforce_org_tpm_rate_limit`) is shared with the chat path; metering it INTERNALLY
  (so every caller meters, chat + MCP) would be a cleaner future refactor than per-caller metering.
