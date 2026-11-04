# Implementation Plan — GW28 multi-model routing + bounded stalled-upstream read

Source of truth: `design.md` (approved, round 8) and `requirements.md` in this directory. This plan
sequences the design's "Implementation order" bottom-up and does not re-decide architecture.
The live Docker/staging gate is OUT OF SCOPE: never run or claim it. Offline gates only.

## Working conventions (read first)

- Worktree: `/work/suraj/AI_Mesh_Firewall/.worktrees/gw28` (branch `gw28-multi-model-routing`). Use
  absolute paths; relative paths land in the parent workspace.
- All commands run from `<worktree>/gateway_v2`. No venv exists in the worktree; use the main repo's venv
  (verified: it imports `gateway_v2` from cwd because `pythonpath = ["."]`):
  `V=/work/suraj/AI_Mesh_Firewall/gateway_v2/.venv/bin`
- Layering (import-linter): edge > admit > plan > detect > resolve > dispatch > egress > audit > runtime >
  contracts > domain. New `dispatch` modules import only `domain` + `dispatch` siblings + stdlib. Bounds
  reach `dispatch` by injection from `edge`/`runtime`.
- House rules: frozen+slotted value types, no module-level mutable state, no capacity literal outside
  `runtime/resources.py` (GW27 D-13 allowlist unchanged), fail closed, HTTP objects only in `edge`,
  functions <= 120 lines, modules <= 800 lines.
- Property tests: seeded `random.Random`, no `hypothesis`, each tagged in source
  `# Feature: gw28-multi-model-routing, Property N: <text>`; pure >= 10,000 iterations, async/fault-script
  >= 200 (requirement floor 100). Files in `gateway_v2/tests/dispatch/` (edge tests in `tests/edge/`).
- Commit locally per task group; never push.

## Exact offline gate commands (from `.github/workflows/gateway-v2.yml`, `structural-gates` job)

Run from `<worktree>/gateway_v2` with `V=/work/suraj/AI_Mesh_Firewall/gateway_v2/.venv/bin`:

```bash
for tree in gateway_v2 state_control; do
  $V/python -m lint.check_sizes "$tree"
  $V/python -m lint.check_http_outside_edge_resolve "$tree"
  $V/python -m lint.check_no_module_mutable "$tree"
  $V/python -m lint.check_capacity_literals "$tree"
  $V/python -m lint.check_frozen_dataclasses "$tree"
  $V/python -m lint.check_dispatch_provenance "$tree"
done
$V/python -m lint.check_tenant_scale gateway_v2     # data plane only
$V/lint-imports                                      # 3 contracts must be kept
$V/ruff check gateway_v2 state_control lint tests
$V/mypy --strict                                     # files list comes from [tool.mypy]
$V/python -m lint.check_console_inventory
$V/python -m pytest -q                               # full offline suite (addopts in pyproject)
```

Also (CI job `secret-headers`, offline): from the worktree root
`python3 gateway_v2/lint/check_secret_headers.py --repo . --fail-allowlist`.

Pre-existing baseline (checked on this branch before any change): `check_sizes` exit 0,
`check_capacity_literals` exit 0, `lint-imports` 3 kept. Known: `check_tenant_scale` on `state_control`
is NOT in the gate (CI runs it on `gateway_v2` only). Record the pre-change full-pytest result once
(task 0) so any later failure can be classified as new vs pre-existing.

## SIZE HAZARD discovered while planning (must be handled in task 7.0)

Module cap is 800 lines and these files are already at the limit: `runtime/resources.py` = 800,
`edge/app.py` = 800, `edge/routes.py` = 799. Every edit in them must be net-zero-or-negative lines or
first extract code. `edge/openai_chat.py` = 712, `edge/dev_profile.py` = 521, `edge/production_profile.py`
= 494 have headroom. Re-run `check_sizes` after every task touching these files.

## Tasks

- [ ] 0. Baseline. Run the full gate list above plus `$V/python -m pytest -q` on the untouched branch and
  record pass/skip/xfail counts and any pre-existing failures in `.agents/tasks/baseline.txt`.
  Files: none (record only; do not commit the .agents dir).
  Verify: commands finish; counts recorded.

- [ ] 1. Posture codes. Add and export `ROUTING_NO_CANDIDATES`, `ROUTING_UNKNOWN_MODEL`,
  `ROUTING_ALL_UNAVAILABLE`, `ROUTING_ATTEMPTS_EXHAUSTED`, `ROUTING_TEARDOWN_UNCONFIRMED`,
  `ROUTING_CONTEXT_MISSING`, `ROUTING_SNAPSHOT_UNAVAILABLE`, `ROUTING_INTERNAL_ERROR` following the
  single-spelling convention of the existing codes (codes only).
  Files: `gateway_v2/gateway_v2/domain/posture.py`; test beside existing posture tests
  (`gateway_v2/tests/domain/`).
  Verify: `$V/python -m pytest -q tests/domain` passes; `$V/mypy --strict` clean.

- [ ] 2. Capacity (depends on 1 only for naming). In `runtime/resources.py` add ONLY the six literals
  (`_UPSTREAM_FIRST_EVENT_MULT=1500.0`, `_UPSTREAM_GAP_FRACTION=0.8`, `_ROUTING_MAX_ATTEMPTS=2`,
  `_ROUTING_MAX_CANDIDATES=8`, `_ROUTING_MAX_WEIGHT=16`, `_ROUTING_MAX_IDENT_LEN=64`). FIRST move the
  literal-free `snapshot()` (L766-800) to new `runtime/resources_snapshot.py` and update its two importers
  (`runtime/pools.py`, `tests/runtime/test_lgw12_resources.py`); no re-export from `resources.py`. Add
  `runtime/resources_routing.py::RoutingBoundsMixin` (function-local import of constants, no literal) with
  accessors `upstream_first_event_timeout_s`, `upstream_gap_timeout_s`, `upstream_total_timeout_s`,
  `routing_pre_content_budget_s(n)`, `provider_pool_share(n)`, `routing_max_attempts`,
  `routing_max_candidates`, `routing_max_weight`, `routing_max_identifier_len` per design C8 (each raises
  `CapacityUnavailable` on non-positive; `provider_pool_share` has no floor). Mix into `ResourceContract`.
  Extend the moved `snapshot()` with the four bound values. Accessor tests incl. P7/P11 derivation clauses
  (random `target_p99_ms` in [1,100]: `0<gap<first_event<total<max_stream_duration_s()`; `gap<inter_chunk`;
  budget > `n*(first_event+cancellation_bound)`).
  Files: `runtime/resources.py`, `runtime/resources_snapshot.py` (new), `runtime/resources_routing.py` (new),
  `runtime/pools.py`, `tests/runtime/test_lgw12_resources.py`, new `tests/runtime/test_gw28_resources.py`.
  Verify (EXIT GATE, must pass before task 3): `$V/python -m lint.check_sizes gateway_v2` and
  `$V/python -m lint.check_capacity_literals gateway_v2` exit 0; `$V/python -m pytest -q tests/runtime`;
  `$V/mypy --strict`.

- [ ] 3. Value types: `dispatch/routing_types.py` (depends 1, 2). `Candidate` (incl. `provider_key`,
  `passthrough`), `CandidateSet.build` (all D-8a/input-validation rules: `_IDENT` fullmatch with re.ASCII,
  `_UPSTREAM_MODEL` for `upstream_model`, weight/size caps injected as args, passthrough only with
  `allow_passthrough=True`, destination checks), `RoutingTable`, `RoutingContext` (+ `from_plan`, invalid
  model stored as `""`), `RoutingSnapshot`, `RoutingRefusal`, `RoutingDecision`, `RoutingConfigError`,
  `is_valid_model_name`, `routing_reason` constants. All `@dataclass(frozen=True, slots=True)`; mappings as
  sorted tuples; the compiled `_IDENT` pattern must be built without module-level mutable state (build via
  a pure function from the injected max length; keep AST gate `check_no_module_mutable` green).
  Files: `dispatch/routing_types.py` (new); test T-edge-6 + validation unit tests
  `tests/dispatch/test_gw28_routing_types.py`.
  Verify: `$V/python -m pytest -q tests/dispatch/test_gw28_routing_types.py`; `check_frozen_dataclasses`,
  `check_no_module_mutable`, `check_capacity_literals`, `mypy --strict`, `lint-imports`.

- [ ] 4. Pure ranking: `dispatch/model_routing.py::ModelRouter` (`__slots__ = ()`; depends 3). Implement
  design C2 steps 0-6 (client-name guard, match, eligibility, key with `\x1f`, prefix-hasher `copy()`
  per vnode, `sorted by (-score, candidate_id)`, return tuple or `RoutingRefusal`). Add a golden-vector
  test pinning byte-identical output vs the unoptimised formula.
  Tests: P1, P2, P3 in `tests/dispatch/test_gw28_routing_determinism.py` (10,000 iterations, tagged),
  incl. `rank` micro-benchmark with budget from `contract.target_p99_ms`.
  Verify: `$V/python -m pytest -q tests/dispatch/test_gw28_routing_determinism.py`; mypy/ruff/lint-imports.

- [ ] 5. Stall guard: `dispatch/stall_guard.py` (depends 1, 3 for `RoutingConfigError`). `StallBounds`
  (ordering validated in `__post_init__`), `TeardownUnconfirmed`, `bounded_events` per C3 (cumulative
  content-indexed pre-content deadline D-14, gap after first content/terminal event, total ceiling, one
  `upstream_timeout` terminal, `finally` aclose within `teardown_s`, never swallow
  `CancelledError`/`GeneratorExit`). Imports `UPSTREAM_TIMEOUT` from `dispatch.openai_upstream` and event
  types from `dispatch.provider`.
  Tests: P7 in `tests/dispatch/test_gw28_stall_bound.py` using small real-asyncio bounds
  (50 ms / 20 ms / 2 s / 20 ms, tolerance `[0.9*bound, bound+0.25 s]`), incl. role-only-then-silence,
  mid-stream stall, slow trickle, closed-iterator observation, and an in-process transport that records
  connection close (Req 5.4 local proof).
  Verify: `$V/python -m pytest -q tests/dispatch/test_gw28_stall_bound.py`; gates as above.

- [ ] 6. `dispatch/routed_provider.py` (depends 3, 4, 5). `RoutedProvider` (set-once `__slots__`, NOT a
  `ProviderClient`), `preflight`, `open_routed` (single sequential attempt loop, ONE `<client>.open(`
  call site, `committed` latch, retryable set D-5, D-7 exhaustion, D-10 empty-iterator, D-17 exception
  policy, helpers `_run_attempt`/`_terminal` to keep functions <= 120 lines), `rebind` (replaces only
  `body[BODY_OPENAI_KEY]["model"]` + destination; passthrough leaves model), construction validation
  (`per_request_safe is True` on `type(client)`, every candidate id has a client, `max_attempts>=1`).
  Add `per_request_safe: ClassVar[bool] = True` and a read-only `connections: int` property to
  `OpenAIUpstreamProvider` (the only edits to that file).
  Tests (files in `tests/dispatch/`, shared fakes in a new `_gw28_fakes.py` modelled on `_b4_fakes.py`):
  P4 `test_gw28_no_double_generation.py`, P5 `test_gw28_stream_integrity.py`, P6
  `test_gw28_no_policy_bypass.py` (incl. AST checks), P8 `test_gw28_attempt_bound.py`, P9
  `test_gw28_trace_redaction.py` (10,000-iteration model-name fuzz, T-edge-9 unit half), P10
  `test_gw28_fault_injection.py`, P12 `test_gw28_seam.py`.
  Verify: `$V/python -m pytest -q tests/dispatch -k gw28`; `$V/python -m pytest -q tests/dispatch`;
  `lint-imports`, `check_dispatch_provenance` (GW28 must never construct a `DispatchAuthorization`),
  `check_http_outside_edge_resolve`, `mypy --strict`, `ruff`.

- [ ] 7.0 Create size headroom BEFORE any edge edit (see SIZE HAZARD). Extract a cohesive, behaviour-free
  block out of `edge/routes.py` (799) and `edge/app.py` (800) into new sibling modules (e.g. the
  `_attempt` open-and-wrap helper or the `_build_chat_route` argument plumbing) so each ends with >= 25
  lines of headroom. Pure refactor, no behaviour change.
  Files: `edge/routes.py`, `edge/app.py`, new `edge/*.py`.
  Verify: `check_sizes gateway_v2` exit 0 with headroom; `$V/python -m pytest -q tests/edge
  tests/openai_conformance` unchanged vs baseline; `mypy --strict`.

- [ ] 7.1 Edge plumbing (depends 6, 7.0). (a) `edge/errors.py` + `edge/wire/sse.py`: additive entries for
  all eight `ROUTING_*` codes (404 `model_not_found` for unknown model; 503 `service_unavailable` for the
  rest) with a test iterating the posture codes. (b) `edge/routing_trace.py` new `RoutingTrace`
  (`__slots__`, bounded by `routing_max_attempts()+1`, drop-and-count, per-request). (c)
  `console_projection.chat_console` / `console_from_holds` gain optional `routing: Sequence[RoutingDecision]
  = ()` mapped to `model_routing` stage `action`/`ran`/`deciding_rule` exactly per the design table
  (5 fields, `-` for None, alias only for non-passthrough served aliases); must pass `validate_console`;
  default `()` keeps existing callers byte-identical. One rate-limited `routing_decision` event-log line
  via `edge/event_log`. (d) `edge/chat_upstream.py` `open_chat_upstream(deps, plan, request, holds)` and
  `routing_context(...)`.
  Verify: `$V/python -m pytest -q tests/edge tests/audit`; T-edge-8 (concurrent identical requests have
  disjoint traces); `mypy --strict`; `check_sizes`.

- [ ] 7.2 Timers + chat wiring (depends 7.1). (a) `edge/stream_timeouts.py`: optional `first_event_s`,
  `_first_pending` one-cell flag, first-await uses it, D-18 idle re-seed after first await returns for
  any event kind; `routing=None`/`first_event_s=None` byte-for-byte unchanged (add regression test). (b)
  `ChatRoute.routing: RoutedProvider | None = None` (`edge/chat_deps.py` or `routes.py`), builds
  `StreamTimeouts(first_event_s=contract.routing_pre_content_budget_s(max_attempts))` only when routing is
  set. (c) Replace the two chat open sites (`routes.py::_next_attempt._attempt`, `openai_nonstream.py`)
  with `open_chat_upstream`; per-request `RoutingTrace` threaded by reference into `console_from_holds`.
  (d) `openai_chat.py::_stage` preflight (config refusals only, zero provider calls, no `mark_dispatched`,
  spend refunded by `RequestHolds.release`). (e) `app.py`: `build_app(..., routing=None)` and
  `_build_chat_route(..., routing=None)`; misc/embeddings/responses/MCP/vector builders untouched.
  Tests: P11 `tests/edge/test_gw28_timer_precedence.py` (incl. D-18 cases), T-edge-1 (exactly one
  `open_routed` per request, fallback inside), T-edge-3, T-edge-4 (audit destination unchanged), T-edge-5
  (secret in fallback output and in mid-stream error_frame redacted/blocked), end-to-end non-streaming
  never-yielding upstream -> 504 within derived bound with fake recording close.
  Verify: `$V/python -m pytest -q tests/edge tests/openai_conformance`; `check_sizes`; AST P12 still green.

- [ ] 7.3 Profiles (depends 7.2). `edge/dev_profile.py::_provider` returns `(provider, routing, log)`:
  single plain `OpenAIUpstreamProvider` unchanged (`timeout_s=max_stream_duration_s()`), `routing` built
  with one `passthrough` candidate in explicit `fallback_set` whose client IS that instance; `routing=None`
  when `provider_url` unset; add `"routing": routing` to the build kwargs; boot log prints the four
  bounds; `closers` unchanged. `edge/production_profile.py`: `build_routed_provider(contract, table,
  client_factory, snapshot_source, *, owned_elsewhere={}, log)` returning frozen `RoutedBundle(routed,
  closers)` (factory-built clients only, `connections=share`, sum <= `pool_size(PROVIDER)` else
  `RoutingConfigError`), logs the bounds line; leaves the fail-closed default provider.
  Tests: T-edge-2 (each surface), T-edge-7 (non-chat surface unaffected by silence, chat gets
  `upstream_timeout`, exactly one `OpenAIUpstreamProvider`), T-life-1 (closers, share arithmetic,
  negative case `P+1` refuses), T-log-1 (both paths).
  Verify: `$V/python -m pytest -q tests/edge`; `mypy --strict`; `check_sizes`.

- [ ] 8. Full offline gate (depends all). Run every command in "Exact offline gate commands" and the
  full `pytest -q`; compare with task 0 baseline: zero new failures. Add tests asserting
  `check_sizes`/`check_capacity_literals` CLIs exit 0 (D-15 regression guard). Confirm: no change to the
  capacity-literal allowlist, no module-level mutable state, all properties tagged in-source, >= 100
  iterations each. Write the PR-description notes (do NOT claim the live gate): 0.8 s mid-stream gap
  behaviour change (non-streaming + routed streaming), 30 s pre-content bound and the
  `_UPSTREAM_FIRST_EVENT_MULT` knob, Req 5 closed for routed chat/dev profile only until GW36 S5,
  residual provider-side generation after timeout fallback, no per-candidate enforcement attestation.
  Verify: all gates exit 0; pytest green vs baseline.
