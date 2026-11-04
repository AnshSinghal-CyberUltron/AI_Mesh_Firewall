# GW29 merge into integration-gw27-35
- Merge commit: 2b278955 (git merge --no-ff gw29-killswitch-isolation onto GW28 note 0e0737e6)
- Handoff: no HANDOFF.md at gw29 root or in .kiro/specs; read docs/plans/evidence/2026-11-04-gw29/ and the AGENTS.md GW29 entry instead.
- Fix commits: none separate. Post-merge fixes (below) were made while the merge was still uncommitted and are folded into 2b278955.

## Conflicts + resolutions (21 files)
- edge/app.py: build_app/_build_chat_route kept ALL of `org_limits`, `routing` (GW27/28) and `control_factory`, `incident_gate`, `incident_recheck` (GW29). Default `control` is GW29's fail-closed `RefusingControlSource` (NOT GW27/28's `NoControlSource`; the GW29 dev profile injects `DevNoControlSource`). GW28's `app_defaults` helpers (`one_stream`, `no_error_findings`) kept; GW29's duplicate private copies dropped. Docstring merged.
- edge/routes.py: ChatRoute fields `routing` + GW29 incident fields; imports from GW29 `route_emit` (emit_response, emit_error_code, ProducedModeHolder) plus GW28 `route_support.placeholder`. Duplicate `ProducedModeHolder` removed from `route_support.py` (single definition in `route_emit`); `placeholder` now imports `emit_response` from `route_emit`.
- edge/openai_chat.py: kept GW27 org-limits phase 5b, GW28 routing preflight, GW29 binding/routed_model/reroute; `with_openai_body(..., model=routed_model, org_ceiling=...)`.
- edge/upstream_body.py: `with_openai_body` takes BOTH keyword-only `model` (GW29) and `org_ceiling` (GW27).
- edge/openai_nonstream.py: `_collect_output_channels(deps, destination, body, holds, org_id, routed_model, plan)`; GW29 recheck + `mark_dispatched(routed_model)`, GW28 `open_chat_upstream`.
- edge/openai_misc.py: GW27 `note_controls`/org gate + GW29 embeddings gate; comments condensed to fit the 800-line gate (was 809).
- edge/request_holds.py, publisher.py, audit_emit.py, production_profile.py, dev_profile.py: union of both sides (slots, fields, imports, wiring, stamp + incident records).
- runtime/resources.py: `ResourceContract(VectorBoundsMixin, RoutingBoundsMixin, IncidentBoundsMixin)`; GW29 `resources_env` import kept; `snapshot()` stays in GW28's `resources_snapshot.py` and gained `**contract.incident_snapshot()`.
- tests (8 files) + AGENTS.md: both sides' imports / changelog entries kept in order.

## Post-merge fixes (folded into the merge commit)
- Size gate: openai_chat.run_lifecycle 125 -> <=120 (comments condensed), openai_misc.py 809 -> <=800, wire/sse.py 801 -> 800 (docstring). Limits not raised.
- tests/runtime/test_lgw29_resources.py imports `snapshot` from `resources_snapshot` (GW28 location).
- tests/edge/test_lgw29_misc_surfaces.py passes GW27's now-required `org_limits` to `_build_chat_route` / `_build_misc_route`.
- test_gw36_s1_upstream_body.py calls pass both `model=` and `org_ceiling=`.
- ruff --fix import ordering in 8 test files.

## Gates (from gateway_v2/, venv /tmp/intv)
| Tool | Result |
|---|---|
| pytest -n 24 (AMF_EDGE_DOCKER=0) | 3880 passed, 108 skipped, 1 failed (test_check_tenant_scale::test_the_shipped_data_plane_is_clean = known pinecone_transport.py:140) |
| ruff check gateway_v2 state_control lint tests | all passed |
| mypy --strict | 8 errors, all import-not-found for optional vector SDKs (baseline); 0 other (320 files) |
| lint-imports | 3 kept, 0 broken |
| check_sizes / http_outside_edge_resolve / no_module_mutable / capacity_literals / frozen_dataclasses / dispatch_provenance x gateway_v2 + state_control | all exit 0 |
| check_secret_headers --repo .. , check_console_inventory | exit 0 |
| check_tenant_scale | only known: pinecone_transport.py:140; state_control/valkey.py:100 |
Live Docker/staging gates not run in this step. Note for owner: GW29 makes an unwired `control` fail closed (streams cut); any custom `build_app` caller must inject a control source or factory.
