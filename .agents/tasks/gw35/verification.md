# GW35 verification (offline gates only)

Branch `gw35-console-truth`, final commit `b79c82d7`. Output below is copied from the runs; long outputs are tail-trimmed as marked. NOT run and NOT claimed: live Docker/staging gate (L35-2), Playwright sweep (L35-1), browser walk (L14b-3), owner signatures. Python gates ran in a venv outside the repo (`/tmp/gw35venv`, `pip install -e .[dev,conformance]` plus `uvicorn[standard]` and `httpx`). Django suites ran in the `ai_mesh_firewall-control` image against a THROWAWAY `pgvector/pgvector:pg16` container on its own docker network (`gw35net`); the live stack database was not touched. The frontend ran in `node:22-alpine` with `npm ci`.

## Full offline pytest (gateway_v2, whole `tests/` tree)
```
$ cd gateway_v2 && python -m pytest tests -q
FAILED tests/gates/test_check_tenant_scale.py::test_the_shipped_data_plane_is_clean
1 failed, 3164 passed, 109 skipped, 9 warnings in 566.41s (0:09:26)
```
The one failure, `tests/gates/test_check_tenant_scale.py::test_the_shipped_data_plane_is_clean`, is pre-existing: `dispatch/vector_transports/pinecone_transport.py:140` ("whole-collection store read"). It fails identically on the base commit `1634da0c` (see `docs/plans/evidence/2026-11-04-gw35/baseline.md`). No other failure. The first full run in this card (before the control and frontend edits) was `2 failed, 3152 passed, 111 skipped`; the second failure there was the console inventory drift, fixed by regenerating the ledgers last.

## Final targeted re-run on the final tree (after the last test additions)
```
$ python -m pytest tests/edge tests/audit tests/domain tests/runtime tests/egress tests/lint tests/gates -q
FAILED tests/gates/test_check_tenant_scale.py::test_the_shipped_data_plane_is_clean
1 failed, 1828 passed, 23 skipped in 232.06s (0:03:52)
```

## Static gates
```
$ ruff check .
All checks passed!

$ python -m mypy --strict gateway_v2   (final run, after installing the serve extra)
gateway_v2/dispatch/vector_transports/weaviate_transport.py:46: error: Cannot find implementation or library stub for module named "weaviate"  [import-not-found]
gateway_v2/dispatch/vector_transports/weaviate_transport.py:139: error: Cannot find implementation or library stub for module named "weaviate.classes.config"  [import-not-found]
gateway_v2/dispatch/vector_transports/qdrant_transport.py:48: error: Cannot find implementation or library stub for module named "qdrant_client"  [import-not-found]
gateway_v2/dispatch/vector_transports/qdrant_transport.py:122: error: Cannot find implementation or library stub for module named "qdrant_client.http.exceptions"  [import-not-found]
gateway_v2/dispatch/vector_transports/pinecone_transport.py:63: error: Cannot find implementation or library stub for module named "pinecone"  [import-not-found]
gateway_v2/dispatch/vector_transports/mongo_transport.py:52: error: Cannot find implementation or library stub for module named "pymongo"  [import-not-found]
gateway_v2/dispatch/vector_transports/elastic_transport.py:137: error: Cannot find implementation or library stub for module named "elasticsearch"  [import-not-found]
gateway_v2/dispatch/vector_transports/elastic_transport.py:203: error: Cannot find implementation or library stub for module named "opensearchpy"  [import-not-found]
gateway_v2/dispatch/vector_transports/chroma_transport.py:45: error: Cannot find implementation or library stub for module named "chromadb"  [import-not-found]
gateway_v2/dispatch/vector_transports/chroma_transport.py:45: note: See https://mypy.readthedocs.io/en/stable/running_mypy.html#missing-imports
Found 9 errors in 6 files (checked 231 source files)

$ lint-imports
--------------------------------------

Gateway v2 layers KEPT
Resolve imports only domain KEPT
No HTTP framework outside edge KEPT

Contracts: 3 kept, 0 broken.
```
mypy: every one of the 9 remaining errors is a missing optional vendor SDK in `dispatch/vector_transports/*` (weaviate, qdrant_client, pinecone, pymongo, elasticsearch, opensearchpy, chromadb) that is not installed in this venv. None is in a file this card touched. Without the `serve` extra there was also `edge/serve.py:380 unused type: ignore`; it disappears once uvicorn is installed. This card's own files are mypy-strict clean (the two errors seen mid-card were fixed).

## AST gates, sizes, inventory, ledger
```
$ python -m lint.check_sizes
exit=0
$ python -m lint.check_capacity_literals
exit=0
$ python -m lint.check_no_module_mutable
exit=0
$ python -m lint.check_frozen_dataclasses
exit=0
$ python -m lint.check_http_outside_edge_resolve
exit=0
$ python -m lint.check_tenant_scale
gateway_v2/dispatch/vector_transports/pinecone_transport.py:140: keys: whole-collection store read: cannot be O(changes)
1 tenant-scale violation(s). See §2.1 rule 1 and R2-02: no work proportional to tenants, keys or records on a serving path.
exit=1
$ python -m lint.check_console_inventory
exit=0
$ python -m lint.admin_ledger
/v1/observability: pending signature
/v1/policy/check: pending signature
/v1/usage: pending signature
exit=0
$ python -m lint.admin_ledger --require-signed
/v1/observability: pending signature
/v1/policy/check: pending signature
/v1/usage: pending signature
exit=1
```
`check_tenant_scale` exits 1 on the pre-existing pinecone finding only (same on `1634da0c`); `state_control/valkey.py:100` is the documented pre-existing finding and is not reported by this gate.

## Frontend (node:22-alpine, `npm ci`)
```
$ npm run lint
> ai-mesh-firewall-ui@0.1.0 lint
> npm run test:unit
> ai-mesh-firewall-ui@0.1.0 test:unit
> node --test "src/utils/*.test.js" "src/components/*.test.js" "src/constants/*.test.js"
# tests 208
# suites 7
# pass 208
# fail 0
# cancelled 0
# skipped 0
$ npm run test:unit
> ai-mesh-firewall-ui@0.1.0 test:unit
> node --test "src/utils/*.test.js" "src/components/*.test.js" "src/constants/*.test.js"
# tests 208
# suites 7
# pass 208
# fail 0
# cancelled 0
# skipped 0
$ npm run build
> ai-mesh-firewall-ui@0.1.0 build
> vite build
✓ 2892 modules transformed.
✓ built in 4.92s
```

## Control plane
```
$ python -m unittest ai_mesh_control.core.tests.test_v2_audit_adapter_gw35 (host, no Django)
----------------------------------------------------------------------
Ran 13 tests in 0.021s

OK
$ manage.py test core.tests.test_retired_routes core.tests.test_v2_audit_adapter_gw35 policy.tests.test_v2_totals_gw35 policy.tests.test_soc_kpis_period policy.tests.test_analytics_rollup policy.tests.test_enforcement_org_scope (control image, throwaway pgvector postgres, no live-stack DB)
Ran 41 tests in 12.475s
OK
```
A wider run that also included the whole `mcp_connector` app was `Ran 91 tests ... FAILED (failures=1, errors=1, expected failures=2)`. Both are environment, not this card: `mcp_connector.tests.test_scan_version_bump` cannot import `fakeredis` (not in the control image), and `test_oauth_transport_guard...test_websocket_ws_scheme_url_is_allowed` needs DNS for `ws-everything.stub`. `manage.py test` against the live-stack database was not run.

## Properties and controls (all inside the pytest run above)
- Properties (seeded `random.Random`, tagged `# Feature: gw35-console-truth, Property N`): P1 1000, P2 1000, P3 500, P5 1000, P7 1000 iterations, P4 200 ASGI requests (`tests/edge/test_gw35_properties.py`); P6 500 + 200 (`control/.../test_v2_audit_adapter_gw35.py`).
- Negative controls (`tests/edge/test_gw35_negative_controls.py`): literal-allow auth/rate_limit, "every failure is policy", and the legacy `_audit_org` each make the named tests fail.
- Golden: `tests/edge/test_gw35_golden_capture.py` compares 11 refusal and failure responses to bytes captured from `1634da0c`; only `zeroshield`, `pipeline_trace`, `source` and `x-zeroshield-action` differ.

## Commits
```
b79c82d7 test(gw35): strict kill, source carrier, projection cost
6266fb46 docs(gw35): progress table row
13edb9fa docs(gw35): evidence summary, baseline, page audit; AGENTS and progress pointers; stream refusal cause
01671b92 feat(gw35): lint vocabulary parity, contract diff, signed-row reporting, retired-route drift test; regenerate GW14b ledgers
3cd9086a feat(gw35): frontend error vocabulary, v2 trace rendering, retired-route panels
255e249e feat(gw35): control adapter error rank, refused_at, v2 totals, retired routes under AMF_GATEWAY_BACKEND
96932bba test(gw35): schema, stage table, stream cuts, failed-credential, properties, negative controls
81bb3bf7 test(gw35): golden capture of pre-change refusals, refusal trace integration tests; wire unauth gate and publisher series
c79fa4e8 feat(gw35): one console builder over request facts, error vocabulary, unauth audit gate
90b31c5d feat(gw35): failure classification table and unauth audit rate bound
```
