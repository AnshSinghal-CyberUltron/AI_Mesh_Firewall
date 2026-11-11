# GW31 RAG pipeline merge into integration-gw27-35
- Merge commit: 5558005c (git merge --no-ff gw31-rag-pipeline onto GW32 note b790504a)
- Fix commit: a8cd6b69 (separate from the merge; one-line import cleanup + tests)
- Handoff: `.kiro/specs/gw31-rag-pipeline/HANDOFF.md` (live Docker/staging gate NOT run; L31-3 against real Chroma and the Req 6.5 plan-feed rule cycle are open).
## Conflicts + resolutions (12 files, all additive unions)
- admit/identity.py, domain/identity.py: `Principal` keeps GW32 `mcp_allowed_tools`, `mcp_max_tool_calls` then GW31 `project` (GW32 tests build Principal positionally through field 7, so `project` goes last); `principal_of` passes all three.
- domain/plan.py: `ExecutionPlan` gets GW31 `rag` placed BEFORE GW27 `controls`, because `test_gw27_controls` pins `controls` as the last field.
- domain/posture.py: GW32 MCP codes and GW31 RAG codes kept side by side.
- edge/app.py: docstring merged (GW31 `rag` noted) and condensed so `build_app` stays at 120 lines (size gate; limit not raised). `rag` param and `build_routes(..., mcp, rag)` kept.
- edge/dev_components.py, edge/dev_plan_feed.py, plan/delta.py: `compile_plan` gets both `controls=` and `rag_settings=`.
- edge/dev_profile.py: imports union; GW32 `_attach_mcp(...)` helper kept, followed by GW31 `attach_dev_surfaces(deps, mcp, env, contract)`.
- plan/compiler.py: `compile_plan(..., controls, rag_settings)`; content hash = `_hash_rules(ordered, controls)` and GW31's non-default-only RAG mix-in on top; plan gets `controls=` and `rag=`.
- plan/document.py: `PlanDocument` fields `controls` then `rag_settings`; encode writes both; decode reads both (`_controls`, `_check_requires` from GW27 plus `_rag_settings`).
- runtime/resources.py: `ResourceContract(VectorBoundsMixin, RagBoundsMixin, RoutingBoundsMixin, IncidentBoundsMixin)`; imports union.
- Route overlap check: edge/routes.py and edge/surface_entries.py auto-merged. GW31 routes are all under `/v1/rag/*`, `/v1/admin/rag/*`, `/v1/vector/*`; GW32 owns `/v1/mcp`, `/gateway/`, `/oauth/*`. No path collision.
## Fix commit a8cd6b69 (tests that encoded a single-branch view)
- edge/routes.py: removed the now-unused `_placeholder` import (ruff F401).
- tests/edge/test_lgw29_misc_surfaces.py: `test_every_post_route_is_classified` placeholder set now lists the GW31 POST routes.
- tests/plan/test_gw31_rag_settings.py: `PlanDocument(..., rag_settings=...)` by keyword (4th positional is now `controls`).
- tests/edge/test_gw31_live_local.py: `Principal(..., project=...)` by keyword. This was a real isolation signal: positional 6th arg now lands on `mcp_allowed_tools`, so both projects collapsed to "default" and read each other's chunks.
## Gates (from gateway_v2/, venv /tmp/intv)
| Tool | Result |
|---|---|
| pytest -n 24 (AMF_EDGE_DOCKER=0) | 4213 passed, 109 skipped, 1 failed (test_check_tenant_scale::test_the_shipped_data_plane_is_clean = known pinecone_transport.py:140) |
| ruff check gateway_v2 state_control lint tests | all passed |
| mypy --strict | 8 errors, all import-not-found for optional vector SDKs (baseline); 0 other (367 files) |
| lint-imports | 3 kept, 0 broken |
| check_sizes / http_outside_edge_resolve / no_module_mutable / capacity_literals / frozen_dataclasses / dispatch_provenance x gateway_v2 + state_control | all exit 0 |
| check_secret_headers --repo .., check_console_inventory, check_credential_flow | exit 0 |
| check_tenant_scale | only known: pinecone_transport.py:140 (gateway_v2), state_control/valkey.py:100 |
Not run: control Django tests, GW31 live Docker/staging gate.
