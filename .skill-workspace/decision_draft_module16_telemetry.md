# Decision draft — Module 1.6 telemetry wiring

## D1 — Canonical `is_module_16_enforcement(meta)` in `policy/module_16.py`
**Reason:** Kill-switch telemetry arrives at `security_risk_score=60` but UI/API used `>= 80`. Semantic isolation classification matches product copy.

## D2 — Replace `critical_only` for 1.6 in ModuleKpisView, ModuleTrendsView, ModuleChartsView, ThreatFeedView
**Reason:** Single backend predicate prevents frontend/backend drift.

## D3 — Merge KillSwitchAuditLog into threat-feed when `module_id=1.6`
**Reason:** User requires control-plane actions in evidence; audit log never reached threat-feed.

## D4 — Frontend: `module_id=1.6` query + isolation MODULE_FILTERS
**Reason:** Server-side filter; drop `criticalOnly: true` alone.

## D5 — Drain: `kill_switch` → module 1.6 in `_EVENT_TYPE_TO_MODULE`; `is_isolation_event=True`
**Reason:** Explicit tags without gateway risk_score change.

## D6 — Django tests + live verification doc
**Reason:** Evidence before completion.

## Rejected for v1
- Gateway-only risk bump to 0.85 (MINIMAL path) — misses audit log.
- Gateway reroute re-check — separate ticket.
