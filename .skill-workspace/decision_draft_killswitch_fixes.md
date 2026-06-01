# Decision draft — post-triage fixes

## F1 — Global scope: one Redis key per org
Disable `__global__` target when **any** row has `model_name=__global__` (ignore `api_key_prefix` for UI reservation). Reason: `build_redis_key()` → `kill_switch:{org}:global`.

## F2 — Fallback: exclude models killed at org-wide **or** form prefix scope
`wouldBlockAsFallback(model)` true when an **active** kill-switch targets that model with `api_key_prefix=""` OR matching form prefix. Reason: gateway checks `org_model` before credential; org-wide row kills all keys.

## F3 — Searchable combobox component
Replace `<select>` with `KillSwitchModelCombobox` (filter by name, provider, model_id). Reason: UX-MAX; many connected models.

## F4 — Backend guard: `__global__` + non-empty prefix
Serializer validation error. Reason: Redis key ignores prefix; DB allows duplicate theater rows.

## F5 — Vitest unit tests for option builders
Pure functions in `killSwitchModelOptions.test.js`. Reason: SKEPTIC edge cases regression-proof.
