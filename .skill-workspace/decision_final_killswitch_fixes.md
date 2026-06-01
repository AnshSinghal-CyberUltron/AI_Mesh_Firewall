# Final decision — kill-switch fixes (post 5-agent triage)

| Item | Verdict | Action |
|------|---------|--------|
| F1 Global one slot | Adopt | `isOrgGlobalKillSwitchReserved` — any `__global__` row blocks second |
| F2 Fallback gateway-aware | Adopt | `wouldModelBeBlockedAsFallback` — active kills at `""` or form prefix; active global blocks all |
| F3 Combobox | Adopt | `KillSwitchModelCombobox.jsx` |
| F4 Backend guards | Adopt | Serializer rejects global+prefix, global+reroute |
| F5 Vitest | Defer | No vitest in frontend package.json |

Also: UI disables reroute when target is `__global__` (matches gateway).
