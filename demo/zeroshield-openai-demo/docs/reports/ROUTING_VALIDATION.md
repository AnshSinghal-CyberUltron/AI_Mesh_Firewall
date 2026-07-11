# Routing Validation Report

## What is validated

- `model="auto"` resolves via gateway governance adjudication (risk/sensitivity/compliance first)
- Full `routing_preferences` contract propagates demo UI → API → SDK `extra_body.routing_preferences`
- `enable_routing`, `data_sensitivity`, and `compliance_requirements` (HIPAA) are honored
- UI routing summary matches backend `zeroshield.routing` / `pipeline` metadata
- Fail-closed behavior when no model satisfies policy (`routing_unsatisfiable`)

## Executable gates

### Demo unit

```bash
cd demo/zeroshield-openai-demo
pytest tests/test_status_reason.py tests/test_routing_scenario.py -q
```

### Demo backend matrix (server on :8765)

```bash
DEMO_URL=http://127.0.0.1:8765 python tests/validation_backend.py
```

Section **E** runs `standard`, `restricted`, and `hipaa` vectors and asserts:

- `routing_preferences` echoed with normalized `data_sensitivity`
- `pipeline.requested_model` / `zeroshield.routing.selected_model` visibility
- `decision_source` and `routing_reason` when routing succeeds
- Explicit `status_reason` on unsatisfied compliance (`routing_unsatisfiable`)

### Demo UI (Playwright)

```bash
cd tests/playwright
DEMO_URL=http://127.0.0.1:8765 npx playwright test demo.spec.mjs -g routing
```

Asserts Routing tab execution, non-placeholder pipeline, routing summary in output + sidebar.

### Gateway routing integrity

```bash
pytest gateway/ai_mesh_gateway/tests/test_routing_pool_hardening.py -q
pytest gateway/ai_mesh_gateway/tests/test_routing_isolation.py gateway/ai_mesh_gateway/tests/test_pipeline_trace_routing.py -q
```

## Manual UI procedure

1. Demo → **Routing** tab — confirm prefs preview shows `enable_routing` + sensitivity
2. Run with `standard` — record routed model + decision source
3. Run with `hipaa` — expect stricter chain or `routing_unsatisfiable` if pool cannot satisfy HIPAA
4. Compare sidebar **Requested / Routed / Decision source** with raw metadata panel

## SDK test

```python
response = client.responses.create(
    model="auto",
    input="Write Python code",
    extra_body={
        "routing_preferences": {
            "enable_routing": True,
            "data_sensitivity": "restricted",
        }
    },
)
routing = (response.model_extra or {}).get("zeroshield", {}).get("routing", {})
assert routing.get("selected_model") or routing.get("routing_status")
```

## Pass criteria

- Routed model or explicit routing status reason on every governed request
- Routing reason / decision source present when multiple models are configured
- Disabled or isolated models never selected (gateway returns 503/422 otherwise)
- UI summary does not diverge from backend routing metadata

## Rollback triggers (block release)

| Symptom | Action |
|---------|--------|
| Missing routing metadata on successful `model=auto` | Block release; inspect gateway `_build_routing_metadata` |
| Sensitivity/compliance has no effect when pool should differentiate | Block release; verify org routing pool + Module 1.5 |
| Disabled/isolated model selected | Block release; run `test_routing_isolation.py` |
| UI summary diverges from backend metadata | Revert demo routing contract + UI mapping together |

## Failure modes

| Symptom | Likely cause |
|---------|--------------|
| Same model regardless of sensitivity | `routing_enabled=false` in org config or single-model pool |
| `routing_unsatisfiable` | No model meets HIPAA/restricted compliance tags |
| `routing_not_configured` | No eligible models in routing pool |
| 422 `model_not_configured` | Model not in org allowlist |
| Empty routing metadata | Upstream stub / simulator mode without routing trace |
