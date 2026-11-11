# GW28 Multi-Model Routing: Handoff

Branch: `gw28-multi-model-routing` (based on `suraj-revamp` 1634da0c). Not pushed, not merged.

## What landed

Source (`gateway_v2/gateway_v2/`):
- `dispatch/`: `model_routing.py` (HRW ModelRouter), `routing_types.py`, `routed_provider.py` (RoutedProvider, per_request_safe), `stall_guard.py`; `openai_upstream.py` (`connections` arg)
- `domain/posture.py`: routing posture codes
- `runtime/`: `resources_routing.py` (routing capacity accessors), `resources_snapshot.py` (snapshot moved out), `resources.py`, `pools.py`
- `edge/`: `chat_upstream.py`, `routing_trace.py`, `routing_boot.py`, `app_defaults.py`, `route_support.py` (size-headroom extractions), plus wiring in `app.py`, `routes.py`, `openai_chat.py`, `openai_nonstream.py`, `console_projection.py`, `dev_profile.py`, `errors.py`, `production_profile.py`, `request_holds.py`, `stream_timeouts.py`, `surface_entries.py`, `wire/sse.py`
- Spec: `.kiro/specs/gw28-multi-model-routing/` (requirements, design, design-review, tasks)

Tests (`gateway_v2/tests/`):
- `dispatch/`: `_gw28_fakes.py`, `test_gw28_{attempt_bound,fault_injection,no_double_generation,no_policy_bypass,routing_determinism,routing_types,seam,stall_bound,stream_integrity,trace_redaction}.py`
- `domain/test_gw28_posture.py`
- `edge/`: `_gw28_app.py`, `test_gw28_{dev_profile,edge_routing,error_maps,timer_precedence}.py`
- `gates/test_gw28_gate_clis.py`
- `runtime/test_gw28_resources.py`
- Adjusted: `edge/test_dev_profile_components.py`, `runtime/test_lgw12_resources.py`

## Gate results (verbatim from `.agents/tasks/gate-results.md`)

# GW28 final OFFLINE gate (live Docker/staging gate NOT run, NOT claimed)
Run from `gateway_v2/` with V=/work/suraj/AI_Mesh_Firewall/gateway_v2/.venv/bin, HEAD 7b6e9186.
Commands per tasks.md / .github/workflows/gateway-v2.yml (structural-gates + secret-headers).

| Gate | Result |
|---|---|
| check_sizes, check_http_outside_edge_resolve, check_no_module_mutable, check_capacity_literals, check_frozen_dataclasses, check_dispatch_provenance x {gateway_v2, state_control} | all 12 exit 0 |
| check_tenant_scale gateway_v2 | exit 1: `gateway_v2/dispatch/vector_transports/pinecone_transport.py:140: keys: whole-collection store read: cannot be O(changes)` / `1 tenant-scale violation(s).` PRE-EXISTING (GW30 commit 564e2ecf; identical in baseline_pytest.txt); GW28 does not touch that file |
| lint-imports | exit 0: `Contracts: 3 kept, 0 broken.` (Analyzed 287 files, 1689 dependencies) |
| ruff check gateway_v2 state_control lint tests | exit 0: `All checks passed!` |
| mypy --strict | exit 0: `Success: no issues found in 291 source files` |
| check_console_inventory | exit 0 |
| check_secret_headers --fail-allowlist | exit 0 |
| pytest -q (full offline) | exit 1: `1 failed, 3157 passed, 104 skipped, 8 warnings in 614.24s (0:10:14)`; sole failure `tests/gates/test_check_tenant_scale.py::test_the_shipped_data_plane_is_clean` (same pinecone finding). Baseline: `1 failed, 1992 passed, 22 skipped` with the SAME single failure => zero new failures |

Property tests (tagged `# Feature: gw28-multi-model-routing, Property N` in source): iterations
P1-3 10,000; P4 300; P5 250; P6 200; P7 100 per scenario; P8 250; P9 10,000; P10 200; P11 200; P12 structural. All >= 100.

## Design deviations (verbatim from `.agents/tasks/deviations.md`)

# GW28 deviations from design.md (iteration 1)

Each item is also written into `.kiro/specs/gw28-multi-model-routing/design.md`, section
"Implementation deviations (iteration 1)", in the same commit as the design fix.

1. **Injected identifier length.** `dispatch` cannot import `runtime`; `max_ident_len` is passed to
   `CandidateSet.build`, `RoutingContext.from_plan`, `ModelRouter.rank` and `RoutedProvider`. Added
   `RoutingTable.build` and `RoutedProvider.serves_alias`. Destination-label cap = same value.
2. **Trace location.** `RoutingTrace` hangs off the per-request `RequestHolds` (`note_routing`)
   instead of a local threaded through `routes.py`/`openai_nonstream.py`; `chat_console` gains
   `routing` + `routing_alias`. Per-request, never keyed by `request_key`, so T-edge-8 still holds.
3. **Event line.** The `routing_decision` line is a plain INFO log from `RoutingTrace.append` (bounded
   by `routing_max_attempts()+1` per request, not rate-limited): `StructuredEventLog` is a closed
   vocabulary and is not reachable from `ChatRoute`.
4. **Responses surface is routed.** It is built from the same `ChatRoute`; the design's "responses
   stays plain" is wrong for the code. Embeddings/moderations/models/MCP/vector stay unrouted.
5. **Factory location.** `build_routed_provider`/`RoutedBundle` are in `edge/routing_boot.py`,
   re-exported by `edge/production_profile.py`.
6. **Duck-typed deps.** `open_chat_upstream` and `_stage` use `getattr(deps, "routing", None)` because
   shipped tests pass bundles without that field (17 tests would otherwise fail).
7. **Preflight code.** `_stage` maps `routing_unknown_model` -> existing `model_not_found` (404).
8. **`OpenAIUpstreamProvider(connections=...)`** constructor argument (default 0), exposed read-only.
9. **Size headroom** by extracting `edge/app_defaults.py` and `edge/route_support.py` (no behaviour change).
10. **Post-commit sink failure** is handled in `_emit(committed=True)` but no code path emits after
    commit (decisions are recorded before each attempt opens), so P10's post-commit clause is a
    unit test of `_emit`, not an end-to-end scenario.
11. **T-edge-5 streaming half** is not covered end to end: the streaming output guard only engages
    under an enforcing OUTPUT rule in the plan and the shipped test plan has none. The non-streaming
    output scan and the non-streaming `error_frame` scan are covered; the streaming path wraps
    `open_chat_upstream` with the same `_as_chunks`/`StreamPipeline` regardless of attempt.
12. **Dev-profile behaviour change (as designed in round 8):** client model names that fail `_IDENT`
    (for example `meta/llama-3`) are now refused with 404 `model_not_found` when `AMF_DEV_PROVIDER_URL`
    is set.

## What remains for the human

- The live Docker/staging gate was NOT run and is NOT claimed. Only the offline gate above was run.
- Pre-existing failure: `check_tenant_scale` / `test_the_shipped_data_plane_is_clean` fails on `dispatch/vector_transports/pinecone_transport.py:140` (GW30, commit 564e2ecf). It is identical in the baseline and untouched by GW28.
- T-edge-5 streaming half is not covered end to end (deviation 11); P10 post-commit clause is unit-level only (deviation 10).
- Dev-profile behaviour change (deviation 12) needs owner awareness.
- Review and merge into `suraj-revamp` is left to the human.
