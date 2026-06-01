# Final decision — Kill-switch model pickers (post triage)

## Triage synthesis (5 agents)

| Decision | MINIMAL | SECURITY | UX-MAX | BACKEND | SKEPTIC | **Final** |
|----------|---------|----------|--------|---------|---------|-----------|
| D1 Select from connected models | Adopt only this | Adopt | Adopt + combobox | Adopt | Reject (free-text OK) | **Adopt** |
| D2 Exclude (model, prefix) reserved | Reject | Adopt (prefix-scoped) | Adopt | Adopt (must match DB) | Reject (breaks multi-prefix) | **Adopt** — matches `unique_together` |
| D3 Fallback minus target + reserved | Reject | Adopt | Adopt | Adopt | Partial reject | **Adopt** |
| D4 Edit keeps current value | Adopt | Adopt | Adopt | Adopt | Adopt | **Adopt** |
| D5 `__global__` target only | Reject | Adopt | Adopt | Adopt | Reject | **Adopt** |

**Overrides:** User requirement and BACKEND/SECURITY outweigh MINIMAL/SKEPTIC on exclusion and global scope. UX-MAX combobox deferred (native `<select>` + provider label + LiteLLM id hint ships now).

## Implementation

- `frontend/src/utils/killSwitchModelOptions.js` — pure filter/build helpers
- `frontend/src/components/KillSwitchPanel.jsx` — selects, load models on modal open, empty states → `?tab=firewall-1-5`
