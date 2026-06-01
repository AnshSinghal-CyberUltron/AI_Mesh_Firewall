# Decision draft — Kill-switch model pickers

## D1 — Replace free-text model fields with selects from `/api/firewall/models/`
**Reasoning:** Operators register `model_name` in Model Connections; free text caused wrong LiteLLM ids and typos. Same source as simulators (`useSimulatorGatewayModels` pattern).
**Reject:** Autocomplete only — still allows invalid strings.

## D2 — Exclude reserved (model_name, api_key_prefix) pairs when creating
**Reasoning:** DB `unique_together` is (org, model_name, api_key_prefix). A model with an existing org-wide kill-switch (empty prefix) must not appear again for empty prefix create.
**Reject:** Hide model globally if any kill-switch exists — blocks valid credential-scoped second switch.

## D3 — Fallback select = connected models minus target minus reserved targets
**Reasoning:** Reroute target must differ from killed model (backend validation). Fallback should not pick a model already under kill-switch for same prefix scope.
**Reject:** Show all models in fallback — user explicitly asked to hide kill-switched models.

## D4 — Edit mode keeps current model_name visible even if “reserved”
**Reasoning:** Editing existing row must not blank the select when model is excluded for create flow.
**Reject:** Force re-pick on edit — breaks inactive kill-switch rows.

## D5 — Include `__global__` in target select only (not fallback)
**Reasoning:** Backend supports org-wide kill-switch scope.
**Reject:** Remove global — operators lose emergency all-models switch.
