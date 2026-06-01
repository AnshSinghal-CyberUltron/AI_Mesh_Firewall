# Triage verdict: Routing audit trail (2026-05-28)

## Final adopted decisions

| ID | Decision | Verdict |
|----|----------|---------|
| D1 | Module 1.5: `sources: ["routing", "agentic_scan"]` + `eventTypes: ["model_routed"]` | **ADOPT** (additive, not routing-only) |
| D2 | `MODULE_SOURCE_MAP["1.5"] = "routing"` | **ADOPT** |
| D3 | `routingEventFields.js` shared helpers | **ADOPT** |
| D4 | Hoist `model_routed` fields in worker + control drain | **ADOPT** (defense-in-depth; T1 rejected but low risk) |
| D5 | `footerPanels` — audit below evidence | **ADOPT** |
| D6 | Pass `threatFeed` into audit panel; remove duplicate fetch | **ADOPT** (T2) |
| D7 | No DB log deletion; no new console noise | **ADOPT** |
| D8 | Tests + build + sign-off artifact | **ADOPT** |

## Agent summaries

- **T1 (frontend-only):** REJECT D4 — sufficient frontend fix. Overruled: hoist is cheap and helps log detail.
- **T2 (shared feed):** REJECT D6 separate fetch — wire props after `MODULE_SOURCE_MAP` fix.
- **T3 (telemetry ops):** UI fixes still required; verify drain if live empty.
- **T4 (keep agentic):** REJECT routing-only D1 — use dual source.
- **T5 (new endpoint):** REJECT — extend threat-feed enum only.
