# CHG-0087 — MCP scan decisions were audited but not metered (Prometheus monitoring gap)

**Change-id:** CHG-0087
**Date:** 2026-07-02
**Severity:** MEDIUM (observability: the 1.4 guardrail decisions were invisible to metrics dashboards/alerting)
**Area:** HARDEN THE ARCHITECTURE — Phase-3 monitoring/metrics (item 13); 1.4 "…→audit" visibility
**Files:** `gateway/ai_mesh_gateway/metrics.py`, `gateway/ai_mesh_gateway/mcp_proxy.py` (+ `tests/test_mcp_scan_metrics.py`)
**Whose work it touches:** the gateway metrics module + the MCP audit sink (`_record_gateway_event`).

## How it was found (monitoring/metrics sweep, item 13)

The gateway HAS a Prometheus metrics layer (`metrics.py`, `/metrics` endpoint, counters for
requests/policy-blocks/rate-limit/kill-switch/stream/chat). But grepping showed **no MCP metric** and
`mcp_proxy.py` imports/calls `metrics` **nowhere**.

## Gap

Every MCP tool-call scan/enforcement decision (block/redact/allow/monitor — for tool-poisoning, credential
force-blocks, PII/IP redaction, tools/list metadata scans, org-scope violations) is written to the MCPEvent
**audit** trail via `_record_gateway_event`, but was **never metered**. So Prometheus dashboards + alerting
could not see MCP block/redact rates, MCP request volume, or the compliance-tag distribution — the 1.4
guardrails were invisible to metrics-based monitoring (the mandate's "monitoring + metrics wired"). Audit
(forensic, per-event) and metrics (aggregate, alertable) are different channels; only the former existed
for MCP.

## Fix

- `metrics.py`: two low-cardinality counters —
  - `amf_gateway_mcp_scan_decisions_total{org, decision}` (decision = block/redact/allow/monitor)
  - `amf_gateway_mcp_compliance_tags_total{org, tag}` (tag = the bounded SECRET/PII/INFRA/HIPAA/PCI-DSS/
    SOC2/GDPR set)
  plus `record_mcp_scan_decision(org_slug, decision, compliance_tags)` — fail-safe (no-op without
  prometheus_client), `_safe_label`-bounded (mirrors `record_request`/`record_policy_block`).
- `mcp_proxy._record_gateway_event`: calls `record_mcp_scan_decision(...)` (best-effort, wrapped in
  try/except so metrics can NEVER break the request path). Every audited MCP decision is now also metered.

## Behaviour after fix (verified)

- `record_mcp_scan_decision("orgA","block",["SECRET","SOC2"])` → `mcp_scan_decisions_total{org=orga,
  decision=block}` +1 and `mcp_compliance_tags_total{org=orga,tag=secret|soc2}` +1 each.
- A `_record_gateway_event(decision=block, tags=[SECRET])` moves the counter (audit sink wired).
- No tags → decision still counted, no tag rows; empty org → `anonymous` fallback.
- prometheus_client absent → `record_mcp_scan_decision` is a no-op (never raises).
- The rows render in `/metrics` (`generate_latest`).

## Verify

```
cd gateway
.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_scan_metrics.py -q   # 4 passed
.venv/bin/python -m pytest ai_mesh_gateway/tests -q -k metric                   # 13 passed
.venv/bin/python -m pytest ai_mesh_gateway/tests -q                             # 1455 passed, 0 failed
```
Broker regression: `services/mcp-broker && .venv/bin/python -m pytest tests -q -k "not websocket"` → 108 passed.

## Residual / follow-ups
- Latency histogram for MCP scan/tool-call (`amf_gateway_mcp_call_seconds`) not added — a future item-13
  enhancement (the audit already carries `latency_ms`).
- Tracing (OpenTelemetry spans) for the MCP per-call chain is a separate item-13 piece.
