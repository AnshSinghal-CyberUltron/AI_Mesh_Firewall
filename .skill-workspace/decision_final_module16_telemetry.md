# Decision final — Module 1.6 telemetry (post-triage)

## Triage synthesis

| Agent | Verdict | Adopted |
|-------|---------|---------|
| MINIMAL | Partial — risk bump alone insufficient | Reject minimal-only path; keep D1–D5 |
| SECURITY | Accept with controls | Org-scoped audit query; `is_audit_log`; `reason_snippet` truncated; synthetic `audit-{pk}` ids |
| BACKEND | Accept | Shared `module_16.py`; merge-then-paginate for `module_id=1.6` |
| UX-MAX | Accept | Control-plane source label in evidence column |
| SKEPTIC | Accept with dedupe | `merge_module_16_feed_items` by `request_id`; drain health left to live verify |

## Final decisions (implemented)

1. **`policy/module_16.py`** — `is_module_16_enforcement`, `module_16_enforcement_q`, audit mapping, merge/dedupe.
2. **Security views** — KPI/trends/charts use semantic 1.6; threat-feed `module_id=1.6` merges audit + enforcement.
3. **`tasks.py`** — `kill_switch`/`model_isolation` → module 1.6; `is_isolation_event` on drain metadata.
4. **Frontend** — `module_id=1.6` feed param; isolation `MODULE_FILTERS`; control-plane source label.
5. **Tests** — `core/tests/test_module_16_telemetry.py`.

## Deferred

- Gateway reroute kill-switch re-check
- Gateway risk_score 0.85 bump (optional polish)
- DB index on `metadata__module_id`
