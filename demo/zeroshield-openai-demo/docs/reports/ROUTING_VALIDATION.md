# Routing Validation Report

## What is validated

- `model="auto"` resolves via gateway adjudication
- `routing_preferences.data_sensitivity` affects model selection
- UI displays requested vs routed model from `zeroshield.routing`

## Test procedure

1. Demo → **Routing** tab
2. Run with `standard` sensitivity — record routed model
3. Run with `hipaa` sensitivity — expect different model or stricter chain
4. Compare `pipeline.requested_model` vs `pipeline.routed_model`

## SDK test

```python
response = client.responses.create(
    model="auto",
    input="Write Python code",
    extra_body={"routing_preferences": {"enable_routing": True, "data_sensitivity": "restricted"}},
)
routing = (response.model_extra or {}).get("zeroshield", {}).get("routing", {})
assert routing.get("selected_model")
```

## Pass criteria

- Routed model present in `zeroshield` metadata
- Routing reason non-empty when multiple models configured
- Block/isolated models never selected (503/422 otherwise)

## Failure modes

| Symptom | Likely cause |
|---------|--------------|
| Same model regardless of sensitivity | `routing_enabled=false` in org config |
| 422 model_not_configured | Model not in org allowlist |
| Empty routing metadata | Upstream stub / simulator mode |
