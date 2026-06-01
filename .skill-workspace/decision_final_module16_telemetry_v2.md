# Decision final v2 — post-triage fixes applied

## Adopted from triage

| Source | Fix |
|--------|-----|
| SECURITY | Removed blanket `source=policy`; audit metadata blocklist; `record_type`; org name on audit rows |
| SKEPTIC/BACKEND | Post-filter enforcement with `is_module_16_enforcement`; narrowed Q; no broad policy bucket |
| MINIMAL | Gateway `_emit_telemetry` bumps kill_switch to 0.85 + `is_isolation_event` |
| UX-MAX | Origin/Operator columns; 1.6 empty state; homepage blocked→containment metrics |
| BACKEND | `streams` in threat-feed response; force module_id=1.6 on isolation ingest |

## Deferred

- Gateway post-reroute kill-switch re-check (SKEPTIC P0 safety — separate ticket)
- JSON index on metadata.module_id
- Origin badge styling / filter chips
