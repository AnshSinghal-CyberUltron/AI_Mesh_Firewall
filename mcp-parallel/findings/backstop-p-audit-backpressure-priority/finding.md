# CHG-0094 — MCP audit backpressure dropped SECURITY-decision records under load

**Change-id:** CHG-0094
**Date:** 2026-07-03
**Severity:** MEDIUM (audit-completeness / observability gap; the `…→tag→AUDIT` chain broke silently under peak load — no data egress, but lost evidence exactly during an attack).
**Area:** HARDEN 1.4 (`authz→minimize→scan+redact→tag→AUDIT`) + item 13/20 monitoring & audit-completeness under peak load.
**Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`_spawn_audit_event` + audit caps), `gateway/ai_mesh_gateway/metrics.py` (`mcp_audit_dropped_total` + `record_mcp_audit_dropped`); `gateway/ai_mesh_gateway/tests/test_mcp_audit_backpressure_priority.py` (new).
**Whose work it touches:** the owning-session proxy audit sink (CP49 fire-and-forget) + the CHG-0087/0088 metrics layer.

## Root cause

`_spawn_audit_event` fires each MCP audit POST off the hot path, bounded by an inflight cap (`_AUDIT_MAX_INFLIGHT=64`) so a slow/serial control plane can't grow the backlog unbounded — correct backpressure. But the drop was **indiscriminate**:

```python
if _AUDIT_INFLIGHT >= _AUDIT_MAX_INFLIGHT:
    LOG.warning("Audit dropped under backpressure (inflight=%s)", _AUDIT_INFLIGHT)
    return False
```

Under the stress scenario (5k–10k concurrent tool calls, control latency near the 5s audit timeout) the 64 inflight slots fill and **every** audit drops — including `block`/`redact`/`rate_limited`/`error` security decisions. An attack that produces many blocks fills the queue and drops the very block/redact audit records it created — the `…→tag→AUDIT` chain breaks precisely when the audit trail matters most, and only a `LOG.warning` (no metric) marked it.

## The fix (CHG-0094)

- **Priority-aware shedding.** Security decisions (`block`/`redact`/`rate_limited`/`error`) get a higher inflight ceiling (`_AUDIT_MAX_INFLIGHT_HIGH=256`, env-overridable); the high-volume `allow`/`monitor`/`clean` records still shed at 64. So a backpressure burst sheds the low-value majority first and the security audits survive. The shared counter still bounds total inflight to the high cap (256) → memory stays bounded.
- **Visibility.** Every drop increments `amf_gateway_mcp_audit_dropped_total{priority,decision}` (via `metrics.record_mcp_audit_dropped`, fail-safe). A non-zero `priority="high"` series is a lost security audit — directly alertable, instead of buried in logs.

`_spawn_audit_event` reads `payload["decision"]` (already present) → no caller change.

## Verification (all green)

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_audit_backpressure_priority.py -q   # 5 passed
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                           # 1552 passed, 0 failed
cd ../services/mcp-broker && ./.venv/bin/python -m pytest tests -q -k "not websocket"           # 108 passed
```

5 new tests drive the REAL `_spawn_audit_event` decision (task-spawn + control POST stubbed):
- below cap → all decisions spawn;
- **at the normal cap (64)** → `allow` sheds (`False`) but `block`/`redact`/`rate_limited`/`error` still spawn (`True`) — the key headroom guarantee;
- `monitor`/`clean` treated as normal (shed);
- **at the hard cap (256)** → even `block` drops but is metered as `priority="high"` (bounded memory + visibility);
- invariant: high cap > normal cap and the priority set is exactly the security decisions.

## Scope / honesty note

This closes an audit-completeness hole in the `…→AUDIT` chain under load and adds a drop metric — it does NOT itself run the 300–500-sandbox live soak that would exercise the backpressure at true scale (host-blocked, items 14–20). It makes that regime SAFE (security audits survive + drops are now observable) and testable. Partial coverage is not completion.
