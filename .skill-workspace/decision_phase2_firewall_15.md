# Phase 2 decision — firewall-1-5 (post-triage synthesis)

## Problems remaining
- `refreshToken` is an opaque side channel (T2 REJECT) — duplicate GETs, clobber risk on silent refresh
- Governance panel still uses spinner on some paths (T4)
- `governance_stale_models` not shown after silent refresh (T5)
- Credential-only edits need forced cache invalidation (T5)
- Touch targets incomplete on routing presets (T4)

## Decisions (D8–D14)

| ID | Decision | Reasoning |
|----|----------|-----------|
| D8 | `FirewallConfigProvider` + `useFirewallConfig` (no new npm deps) | T2: one GET, `invalidate()` on real events; replaces `modelsRefreshToken` |
| D9 | Skip context→form sync when panel `isDirty` | T2/T3: prevents silent refresh clobbering edits |
| D10 | `onConnectionsMutated` always `invalidate()` after save/delete/toggle | T5: credential changes bypass signature dedupe |
| D11 | Extend signature: `model_id`, `provider`, `api_key_set` | T5: metadata-only changes still refresh when list fetch runs |
| D12 | `PanelLoadingShell` skeleton; `refreshing` overlay not full unmount | T4: stable layout, no CLS |
| D13 | Stale allowlist info banner (not old amber removal copy) | T5: surface `governance_stale_models` for operator awareness |
| D14 | Keep `showGatewayCatalog={false}` on 1.5 | User + T1; T3 rejected removal but user explicitly requested |

## Out of scope this pass
- Full React Query/SWR dependency (D8 uses Context)
- Re-adding gateway catalog on 1.5
