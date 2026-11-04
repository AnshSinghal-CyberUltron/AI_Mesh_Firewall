# Implementation Plan: GW29 Incident response (kill switch, isolation, reroute, auto-isolation)

Source of truth: `design.md` (revision 7) and `requirements.md` in this directory. Where this plan names a
design section ("D§4.6") it points at the design; do not re-decide anything the design settled.
Offline only: there is NO live Docker/staging gate in this plan. Live L29-1..L29-4 stay declared OPEN.

## Conventions every task must honour

- Worktree `/work/suraj/AI_Mesh_Firewall/.worktrees/gw29` (branch `gw29-killswitch-isolation`). Work from
  `gateway_v2/` (source `gateway_v2/gateway_v2/`, tests `gateway_v2/tests/`). Commit locally, never push.
- Bottom-up import-linter order: `edge > admit > plan > detect > resolve > dispatch > egress > audit > runtime >
  contracts > domain`. `state_control` imports only `gateway_v2.domain`. `egress` never imports `detect`.
- No capacity literal outside `runtime/resources.py` (lint gate `check_capacity_literals`; protocol limits such as
  `MODEL_NAME_MAX` are named `Final` constants in `domain/incident.py`).
- No module-level mutable state (tuples, `MappingProxyType`, compiled `re.Pattern` only). Value types are
  `@dataclass(frozen=True, slots=True)`. Fail closed everywhere; no `except Exception: pass`.
- HTTP objects (`Retry-After`, status, headers) exist only in `edge/`. `admit/` returns values (`GateVerdict`).
- Canonicalise model identity (`domain.incident.canonical_model`) BEFORE any kill/isolation matching, and send the
  canonical name upstream.
- Property tests: seeded `random.Random`, design says 10 000 iterations (the floor is >= 100), seed in every
  assertion message, no `hypothesis`, and the in-source tag as the first lines of the file:
  `# Feature: gw29-killswitch-isolation, Property N: <text>` and `# Validates: Requirements ...`.
  Test files are `tests/<area>/test_lgw29_*.py`.
- Never leave the tree red: every task ends with its listed verify command green, then the lgw29 subset
  (`pytest -q -k lgw29`) green.

## SIZE HAZARDS (check_sizes: module <= 800 lines, function <= 120 lines) -- read before editing

Measured in this worktree before any GW29 edit:

| File | Lines | Rule |
|---|---|---|
| `runtime/resources.py` | **800 (at cap)** | Free headroom FIRST (task 3), see below. |
| `edge/app.py` | **800 (at cap)**; `build_app` 119 lines (function cap) | Net-zero or negative edits only; move the moved `_NoControlSource` class out (task 17). |
| `edge/routes.py` | **799**; `_stream` 81, `_next_attempt` 79 | Put `first_attempt_refusal`, refusal rendering and the stream-binding helpers in NEW modules; leave only call lines in `routes.py`. |
| `edge/openai_misc.py` | 764; `handle` 88 | New helper module for the incident preamble; add <= ~25 lines. |
| `edge/publisher.py` | 748 | Add the incident adapter in a new module (`edge/incident_publish.py`); <= ~15 lines in `publisher.py`. |
| `edge/serve.py` | 720 | Docstring/default only. |
| `edge/openai_chat.py` | 712; `run_lifecycle` **114 (function cap 120)** | Extract the phase 4b block + binding build into `edge/incident_phase.py`; `run_lifecycle` gains <= 6 lines. |
| `state_control/projector.py` | 626; `desired_state` 81 | Put the org-model projection into a new `state_control/incident_scopes.py`. |
| `edge/openai_responses.py` 511, `edge/production_profile.py` 494, `edge/errors.py` 477, `state_control/writer.py` 471, `edge/state_runtime.py` 462 | ok | Still run `check_sizes` after each edit. |

Rule: before editing any file within 60 lines of the cap, extract first, then add. Run
`python -m lint.check_sizes gateway_v2` after EVERY edge task. New modules are split by responsibility so each
stays well under 800 lines.

**`runtime/resources.py` headroom (task 3).** The constants (`_RISK_*`, `_AUTO_ISOLATE_WINDOWS`, ...) MUST live in
`resources.py` (the gate exempts only that file). Follow the GW30 precedent (`runtime/resources_vector.py`
`VectorBoundsMixin`): put the incident ACCESSOR methods in a new `runtime/resources_incident.py`
(`IncidentBoundsMixin`, no fields, no `__slots__`, reads constants via function-local import from
`runtime.resources`), and keep only the constants (~35 lines) plus one `class ResourceContract(VectorBoundsMixin,
IncidentBoundsMixin)` change in `resources.py`. To make room, move the pure env-parsing helpers `_env_map`,
`_parse_override`, `_parse_owner_queue_ms` (about 40 lines near `resources.py:637`) into a new
`runtime/resources_env.py` and import them back (they hold no capacity literal; verify with the capacity gate).
`resources.py` must end <= 800 and `snapshot()` must call a mixin method `incident_snapshot()` (one line) instead of
inlining 16 new keys. `runtime/resources_org.py` (GW27) is NOT used or extended.

## Gate commands (exact; run from `gateway_v2/`)

Task 1 creates `docs/plans/evidence/2026-11-04-gw29/gates.sh` containing these commands with the container image
chosen there. Container-run style (as GW36 S1-S4 evidence): bind-mount the worktree, install the `dev`,
`conformance` and `serve` extras plus `pytest-xdist`, run with `AMF_EDGE_DOCKER=0`.

```bash
WT=/work/suraj/AI_Mesh_Firewall/.worktrees/gw29
IMG=python:3.12-slim   # task 1: confirm it pulls/runs offline-cached; else use the locally built
                       # ai_mesh_firewall-gateway-v2:latest base. Record the choice in gates.sh.
RUN='docker run --rm -e AMF_EDGE_DOCKER=0 -v '"$WT"':/work -w /work/gateway_v2 '"$IMG"' sh -c'
INST='pip install -q -e ".[dev,conformance,serve]" pytest-xdist >/dev/null'

# G1 full offline suite (baseline in task 1, final in task 22)
$RUN "$INST && pytest -n 24 -q -p no:cacheprovider --junitxml=/tmp/junit.xml 2>&1 | tail -15"
# G1b lgw29 subset (after every task)
$RUN "$INST && pytest -n 8 -q -k lgw29"
# G2 types, lint, layering
$RUN "$INST && mypy --strict"            # pyproject [tool.mypy] files = gateway_v2 state_control audit_control load_harness lint
$RUN "$INST && ruff check ."
$RUN "$INST && lint-imports"             # expected: Contracts: 3 kept, 0 broken (no new ignore)
# G3 the AST gates, each over BOTH trees; every exit must be 0
for g in check_capacity_literals check_no_module_mutable check_frozen_dataclasses \
         check_http_outside_edge_resolve check_sizes check_tenant_scale \
         check_dispatch_provenance; do
  for t in gateway_v2 state_control; do
    $RUN "$INST && python -m lint.$g $t; echo $g $t exit=\$?"
  done
done
$RUN "$INST && python -m lint.check_secret_headers; echo check_secret_headers exit=\$?"
# G4 gate self-tests
$RUN "$INST && pytest -q tests/gates tests/lint"
```

Known acceptable finding: `state_control/valkey.py:100` trips `check_tenant_scale` (pre-existing, R2-05); record it,
do not touch it. Any other non-zero exit is a defect. No new `type: ignore`, no new import-linter ignore.
If the Postgres DSN `AMF_PG_DSN` is unset, the Postgres-16 projector tests skip with a stated reason (declared).

## Tasks (bottom-up; each leaves the tree buildable)

- [ ] 1. Baseline, evidence skeleton and verify-first grep (no product code).
  Create `docs/plans/evidence/2026-11-04-gw29/` with `gates.sh` (commands above, image confirmed by actually running
  G1 once), `00_baseline.txt` (pass/skipped/xfailed counts, mypy file count, import-linter result, each gate exit) and
  `01_caller_grep.txt` = output of `grep -rn "_NoControlSource\|build_app(\|_stream_response\|collect_and_render\|
  run_lifecycle\|collect_and_enforce\|_collect_output_channels\|with_openai_body\|_proceed(\|SERIES_COUNT\|
  KillSwitchSnapshot(" gateway_v2 tests` with each hit classified (needs `outcome=`/`model=`/`control=`, or not).
  Also record `wc -l` of every file in the SIZE HAZARDS table.
  Files: `docs/plans/evidence/2026-11-04-gw29/*`.
  Verify: G1 completes; record the baseline numbers (all later runs must have 0 failed and no new skips except the
  declared Postgres-only tests).

- [ ] 2. Domain value types, scope grammar, canonical model, posture codes (`domain/` only; D§3, D§4.3, D§4.6, D§5.1).
  Create `gateway_v2/domain/incident.py`: `Final` protocol constants (`MODEL_NAME_MAX=128`, `SLUG_MAX=64`,
  `REASON_MAX=200`, `ROUTING_SENTINEL="auto"`, `KS_SCOPE_GENERATION=2`), compiled `MODEL_NAME`/`MODEL_NAME_CANON`/slug
  patterns, `INCIDENT_CAUSES` and `AUTO_REASONS` tuples (include `recheck`, `gate_error`, `unwired`, `unresolvable_model`,
  `unknown_model`, `reroute_unsupported`, `cut_*`), frozen+slotted `IsolationRecord`, `IsolationOrigin`, `ModelStatus`,
  `ModelProfile`, `RequestConstraints`, `RouteDecision`, `RouteOutcome`, `StreamBinding`, `RerouteNote`,
  `IncidentEvent`, `IncidentEventKind`, `KillEvaluation`, `GateVerdict` (with `__post_init__` exactly-one invariant and
  `proceed`/`refuse` constructors), `ModelOutcome`, `SweepResult`; protocols `Notifier`, `FallbackPolicy`,
  `ModelCatalog`, `IncidentDecider`, `ConstraintSource`; fail-closed defaults `fail_closed_incident`,
  `fail_closed_recheck`, `fail_closed_org` (cause `unwired`); pure helpers `canonical_model` (strip, NFKC, ASCII only,
  casefold, single-hop alias, charset, length, control chars -> None), `parse_scope(key, aliases)` (raises
  `StoreDataUnavailable`; quarantine result for known shape + bad model), `org_model_scope`, `isolation_scope`,
  `parse_isolation_body` (missing `origin` = operator; operator needs finite `expires_at` or `no_expiry:true`; bad
  fields -> `malformed=True`; reason `[ -~]{0,200}`). Edit `domain/posture.py`: add `KILL_SWITCH_ENGAGED`,
  `MODEL_KILLED`, `MODEL_ISOLATED_NO_FALLBACK`, `REROUTE_REFUSED`, `STREAM_MODEL_ISOLATED` to the codes and `__all__`.
  Tests: `tests/domain/test_lgw29_incident_types.py` (grammar valid/invalid matrix, canonical_model matrix incl.
  case/whitespace/NFKC/alias/over-long/NUL, isolation parser matrix incl. the four `origin` rows of D§13, frozen/slots
  assertions, `GateVerdict` invariant) and property P13a inside `tests/domain/test_lgw29_canonical_property.py`
  (idempotence: `canonical_model(canonical_model(x)) == canonical_model(x)`; tag `Property 13`).
  Files: `gateway_v2/domain/incident.py`, `gateway_v2/domain/posture.py`, the two test files.
  Verify: G1b, `mypy --strict`, `ruff check .`, `lint-imports`, `check_frozen_dataclasses gateway_v2`,
  `check_capacity_literals gateway_v2`, `check_no_module_mutable gateway_v2` all clean.

- [ ] 3. Resource contract: headroom split, then incident accessors (D§8). Depends on 2 only for names.
  (a) Extract `_env_map/_parse_override/_parse_owner_queue_ms` to `runtime/resources_env.py` and import them back; run
  the full suite subset `pytest -q tests/runtime` to prove no behaviour change; `wc -l resources.py` drops by ~35.
  (b) Add the named constants to `runtime/resources.py` (`_RISK_WINDOW_S`, `_RISK_BUCKETS`, `_RISK_MIN_SAMPLES`,
  `_RISK_PROBATION_MIN`, `_RISK_THRESHOLD`, weights, `_RISK_MODELS_DIV`, `_AUTO_ISOLATE_WINDOWS`, `_HOLD_CAP_MULT`,
  `_RISK_MIN_ORGS`, `_REVOKE_SLOTS_MULT`, `_EVENT_QUEUE_SLOTS`, `_NOTIFIER_TIMEOUT_MULT`, `_RETRY_JITTER_FRACTION`) and
  create `runtime/resources_incident.py` `IncidentBoundsMixin` with every accessor of the D§8 table plus
  `_check_incident_ordering` and `incident_snapshot()`; make `ResourceContract(VectorBoundsMixin, IncidentBoundsMixin)`;
  `snapshot()` merges `incident_snapshot()`. Fail closed with `CapacityUnavailable`/`CapacityUnset`; accessors read the
  constants by function-local import (GW30 precedent).
  Tests: `tests/runtime/test_lgw29_resources.py` (every accessor value, ordering check raises on a violated relation,
  snapshot contains the keys, `risk_bucket_s()*buckets == window`).
  Files: `gateway_v2/runtime/resources.py`, `runtime/resources_env.py`, `runtime/resources_incident.py`, test.
  Verify: `check_sizes gateway_v2` exit 0 (resources.py <= 800), `check_capacity_literals gateway_v2` exit 0, G1b,
  `pytest -q tests/runtime`, mypy, ruff.

- [ ] 4. Audit constructors (D§5.11). `audit/record.py` is 229 lines; additive only.
  Add the closed reason values `TERMINATED`, `REROUTED`, `ISOLATION_CHANGE` to the audit reason tuple (not to
  `edge/counters.py` `REASON_VOCAB`), and constructors `incident_refusal(...)`, `incident_cut(...)`,
  `incident_reroute(...)`, `incident_change(...)` built on `admission(...)` (phase `ADMISSION`, `detail.cause`,
  `detail.status`, `retry_after_ms`; no content/key/credential field). Tests: `tests/audit/test_lgw29_audit_records.py`
  (one record per call, closed vocab, no secret-bearing key present, existing `admission` callers byte-identical).
  Files: `gateway_v2/audit/record.py`, test.
  Verify: `pytest -q tests/audit`, G1b, mypy, ruff.

- [ ] 5. Pure routing decision (`dispatch/`, D§4.5). Depends on 2.
  Create `gateway_v2/dispatch/incident_routing.py` `decide_route(...)` implementing eligibility rules 1-7 (named, distinct,
  not killed, not isolated, allowed_models, constraints known + profiles present, compliance superset, sensitivity
  ceiling) -> `RouteDecision`; no clock, no random, no module state, no I/O. `DispatchRouter` untouched.
  Tests: `tests/dispatch/test_lgw29_routing.py` (one example per rule + malformed isolation) and
  `tests/dispatch/test_lgw29_routing_property.py` (Property 4 determinism + never names a killed/isolated model;
  Property 5 never lowers compliance tags/sensitivity, unknown constraints always refuse; tag lines in source).
  Files: `gateway_v2/dispatch/incident_routing.py`, 2 tests.
  Verify: `pytest -q tests/dispatch -k lgw29`, `lint-imports`, mypy, ruff.

- [ ] 6. KillSwitchSnapshot: org-model set, isolation map, `evaluate`, drift latch, quarantine (`admit/killswitch.py`, D§4.3, 4.6).
  `killswitch.py` is 289 lines. Add `aliases: Mapping[str,str] = MappingProxyType({})` constructor arg (the only
  construction site is `edge/state_runtime.py`, left for task 18), `_org_models`, `_isolations`, `_quarantined`, replace
  `_set_scope` by `_apply_record_locked` using `parse_scope`, `on_count` accounting including isolations and quarantined
  records, `_refreshed_at = None` on EVERY `StoreDataUnavailable` raised (drift latch, unknown shape, malformed `on`),
  `evaluate(org_id, model, now=None) -> KillEvaluation` (one lock acquire, stamp check after), `isolation_record(model)`,
  `isolation_models()`, `expired_count`-support accessors that copy under the lock, and `KillSwitchView.org_models` /
  `.isolations` / `.quarantined` with defaults so existing constructions compile. Keep `scope_is_on`, `killswitch_applier`,
  `killswitch_adopter`, `engaged_scopes` signatures.
  Tests: `tests/admit/test_lgw29_killswitch_scopes.py` (grammar, explicit OFF re-admit, engage/disengage in one refresh
  AC6, drift is STALE immediately C3, quarantine counts toward `on_count`, unknown shape still raises, single-lock
  `evaluate`, old-applier/new-record round trip AC15 with an embedded pre-GW29 `_set_scope` copy) and
  `tests/admit/test_lgw29_stale_property.py` (Property 3: STALE/never loaded/random manifest drift is never admitted).
  Files: `gateway_v2/admit/killswitch.py`, 2 tests.
  Verify: `pytest -q tests/admit`, G1b, `check_sizes`, mypy, ruff, `check_no_module_mutable gateway_v2`.

- [ ] 7. IncidentState: the one read model and the one expiry decision (`admit/incident_state.py`, D§4.4, 5.3).
  Create `IncidentState` (`status_of`, `active_isolation`, `sweep`, `add_local`, `snapshot_models`, `expired_signed_count`)
  with injected `wall`/`clock`, `__slots__`, its own lock never nested with the snapshot lock (reads `switches.*` accessors
  first, then takes its lock), explicit `expires_at` rule, auto-origin duration + `max_isolation_hold_s()` monotonic cap,
  operator `no_expiry`, malformed -> refuse that model only, signed-OFF clears local auto entry and calls probation hook,
  `add_local` tightening only (refuses over an active operator isolation or `model:` kill, evicts earliest expiry at
  `local_isolation_slots()`), `sweep()` returning `SweepResult(local_expired, signed_expired)` each model once with
  bounded `_first_seen`/`_was_active`.
  Tests: `tests/admit/test_lgw29_incident_state.py`, `tests/admit/test_lgw29_expiry_property.py` (Property 6: gate and
  `snapshot_models` agree under random clock jumps and missing/malformed `expires_at`), `tests/admit/
  test_lgw29_lock_order.py` (concurrent `sweep`/`active_isolation`/`_absorb` under a 2 s watchdog + AST check that no
  `with self._lock` body calls `self._switches.*`).
  Files: `gateway_v2/admit/incident_state.py`, 3 tests.
  Verify: `pytest -q tests/admit -k lgw29`, mypy, ruff, `check_frozen_dataclasses`, `check_no_module_mutable`.

- [ ] 8. IncidentGate (`admit/incident_gate.py`, D§4.1, 5.4). Depends on 2, 3, 5, 6, 7.
  `IncidentGate.decide` (step 0 `canonical_model`/`auto` handling per D§4.6; stale -> kill order global/org/org-model/model;
  isolation via `IncidentState` + `decide_route`; every route target re-evaluated), `recheck`, `decide_org`; one broad
  catch at the boundary returning `refuse(KILL_SWITCH_UNAVAILABLE, cause="gate_error")` with ERROR log of the class
  only; `retry_after_s = max(MIN_RETRY_AFTER_S, gap_retry_after_s(...)) + jitter` from injected `rng` bounded by
  `incident_retry_jitter_s()`; `emit` of `REROUTE` events. Also `InterimConstraintSource`/`ModelCatalog` loader goes to
  task 17, not here. Update `admit/__init__.py` exports.
  Tests: `tests/admit/test_lgw29_gate.py` (order, AC1-AC5, AC7 org from argument only, AC13 org-model scope, unknown
  model, `auto` with/without target), `test_lgw29_gate_property.py` (Property 1 engaged applicable scope never admitted,
  Property 2 other orgs/models unaffected), `test_lgw29_model_identity_property.py` (Property 13 mutations of a killed
  model are refused/rerouted, alias kill is a family kill), `test_lgw29_verdict_property.py` (Property 15 GateVerdict
  invariant on every gate input).
  Files: `gateway_v2/admit/incident_gate.py`, `admit/__init__.py`, 4 tests.
  Verify: `pytest -q tests/admit -k lgw29`, `lint-imports`, mypy, ruff, G3 `check_http_outside_edge_resolve` (no HTTP object in admit).

- [ ] 9. Risk tracker, auto-isolator, revocation ledger, incident metrics (`admit/`, D§5.5-5.7, 5.10).
  Create `admit/model_risk.py` (`ModelRiskTracker`: bucket ring, weights from contract, distinct-org sets `orgs_seen` /
  error orgs, only catalogued models, `max_models`, probation, never raises), `admit/auto_isolation.py` (`AutoIsolator`:
  trigger rule with `min(risk_min_distinct_orgs(), distinct_orgs_seen)`, fallback pre-check via `decide_route`, skipped
  events `no_fallback`/`fallback_ineligible`, ISOLATE/RECOVER only for local entries, `sweep`), `admit/revocation.py`
  (`RevocationLedger` on `record.feed_seq`, `floor_feed_seq=-1`, overflow floor, expiry), `admit/incident_metrics.py`
  (`SERIES_COUNT = 27` plus top-K family accounting hook, all real zeros).
  Tests: `test_lgw29_risk_property.py` (Property 7: score in [0,1], 0 below min samples, monotone, probation; 50% 429 =
  0.25 no trip, 100% 429 = 0.5 trips), `test_lgw29_outcome_property.py` (Property 14), `test_lgw29_autoisolate_property.py`
  (Property 12 tightening only), `test_lgw29_autoisolate_dedup.py` (8 trackers -> 8 events, one dedup key),
  `test_lgw29_revocation_property.py` (Property 11), `test_lgw29_incident_metrics.py` (`len(series()) == SERIES_COUNT`).
  Files: the four new `admit/` modules, `admit/__init__.py`, 6 tests.
  Verify: `pytest -q tests/admit -k lgw29`, mypy, ruff, all admit-side AST gates.

- [ ] 10. Checkpoint A: lower layers complete. Run G1 (full), G2, G3, G4; compare with baseline (0 failed). Fix only
  regressions caused by tasks 2-9. Commit per layer so far (domain, runtime, audit, dispatch, admit).

- [ ] 11. Edge value plumbing: errors, counters, holds, deps (small files, no behaviour yet; D§6.1, 6.2, 6.4a, 5.10).
  `edge/errors.py`: envelopes for the 5 new codes (HTTP 503 / stream frame), `INCIDENT_HTTP_CODES` frozen tuple,
  `render_http(..., retry_after_s=None)` adds integer `retry-after = max(1, ceil(...))` and `x-should-retry: false` only
  when given. `edge/counters.py`: `POSTURE_CODES` += the five codes (do NOT extend `REASON_VOCAB`/`STATUS_VOCAB`).
  `edge/request_holds.py`: add slots `_incident_cause`, `_incident_underlying`, `_reroute` with `note_incident_cause`,
  `note_reroute` and read properties (slots test must still show no `__dict__`). `edge/chat_lifecycle.py` /
  `LifecycleOutcome`: additive defaulted fields `routed_model`, `reroute`, `binding`, `retry_after_s`, `cause`.
  `edge/chat_deps.py`: `incident_gate: IncidentDecider = fail_closed_incident`. `edge/upstream_body.py`:
  `with_openai_body(..., *, model: str)` REQUIRED keyword, body `"model": model`; update every caller listed in
  `01_caller_grep.txt` in this same task so the tree stays green (callers pass `model=req.model` for now).
  Tests: `tests/edge/test_lgw29_refusal_headers.py` (headers present for incident codes, absent/identical otherwise),
  `test_lgw29_holds_slots.py`, extend the series-count test later (task 18).
  Files: `edge/errors.py`, `counters.py`, `request_holds.py`, `chat_lifecycle.py`, `chat_deps.py`, `upstream_body.py`, tests.
  Verify: `pytest -q tests/edge`, `check_sizes gateway_v2`, mypy, ruff.

- [ ] 12. Phase 4b in the chat lifecycle (D§6.1, 6.2). Depends on 8, 11. SIZE: extract first.
  Create `edge/incident_phase.py` holding the phase 4b helper (call `deps.incident_gate.decide`, on refuse
  `holds.note_incident_cause` and return `_refused(...)` with `retry_after_s`/`cause`; on proceed build `StreamBinding`
  from `identity.org_id`, `verdict.routed_model`, the already-derived `hashed_bearer_key`, `identity.principal.feed_seq`;
  `holds.note_reroute` when `verdict.reroute`). `run_lifecycle` calls it between `holds.note_request` and `self._admit`
  (net <= +6 lines; confirm `run_lifecycle` < 120 lines). `_proceed(self, plan, req, decision, request_id, *, binding,
  verdict)` passes `verdict.routed_model` to BOTH `GatewayRequest(model=...)` and `with_openai_body(..., model=...)`
  and stamps `binding`/`routed_model`/`reroute` on the outcome. Do not touch `_request_id`. `_refused(...)` takes
  `retry_after_s`, `cause`. Update direct `_proceed(` callers/tests with the two keywords.
  Tests: `tests/edge/test_lgw29_phase_order.py` (AST/behaviour: 4b before `_admit` and before budget spend, refusal =
  zero slot, zero spend, zero provider calls), `test_lgw29_upstream_model.py` (case-mutated and alias names -> canonical
  name in `provider.open` body and `GatewayRequest.model`, request id unchanged, reroute carries fallback),
  `test_lgw29_refusal_charges_nothing_property.py` (Property 9 with the real `QuotaGate` and a fake provider ledger).
  Files: `edge/incident_phase.py`, `edge/openai_chat.py`, `edge/chat_lifecycle.py` (if needed), 3 tests.
  Verify: `pytest -q tests/edge`, `check_sizes gateway_v2` (openai_chat.py and run_lifecycle under cap), mypy, ruff.

- [ ] 13. Refusal rendering, single audit record, stream-vs-HTTP (D§6.4a). Depends on 4, 11, 12. SIZE: routes.py at 799.
  Create `edge/incident_render.py` (incident-aware `_render_refusal` branch: codes in `INCIDENT_HTTP_CODES` render as
  HTTP 503 even when `req.streaming`, passing `outcome.retry_after_s`/`cause`; `_emit_error_code(..., retry_after_s=)`).
  Move any needed lines out of `routes.py` into this module so `routes.py` ends <= 799 (net zero or smaller).
  `edge/audit_emit.py`: suppress the generic admission `REJECTED` record when `holds.incident_cause` is set; derive the
  refused code from `holds.incident_underlying` when cause is `recheck` and `refuse_code` is None; add
  `emit_incident_refusal`, `emit_incident_cut`, `emit_incident_reroute`, copy `RerouteNote` into `detail.routing`.
  Tests: `tests/admit/test_lgw29_verdict_property.py` extension is NOT here; add `tests/edge/test_lgw29_single_audit.py`
  (count all audit records over one whole `handle()`: exactly one for each refusal path, one terminal record per cut)
  and `tests/edge/test_lgw29_streaming_refusal_http.py` (killed org + streaming request -> 503, `retry-after`,
  `x-should-retry: false`, no `text/event-stream`, no SSE head).
  Files: `edge/incident_render.py`, `edge/routes.py`, `edge/audit_emit.py`, 2 tests.
  Verify: `pytest -q tests/edge`, `check_sizes gateway_v2`, mypy, ruff.

- [ ] 14. Mid-stream source, recheck, signature changes and callers (D§4.2, 5.8, 6.3, 6.3a, 6.4). Depends on 9, 12, 13. SIZE: routes.py/app.py.
  Create `edge/inflight_source.py` (`KillSwitchControlSource` using `evaluate`, `age_s=0.0` when fresh and
  `available=False` when STALE, `revoked` via the ledger, `plan_changed=False` as a named constant with docstring;
  `KillSwitchControlFactory`, `RefusingControlSource`, `DevNoControlSource`; a raising source returns
  `available=False`). Move `_NoControlSource` out of `edge/app.py` (net negative for app.py; keep `build_app` < 120
  lines) and make the default `RefusingControlSource()`; `edge/dev_profile.py` and `dev_components.py` pass
  `DevNoControlSource()`; migrate every test caller recorded in `01_caller_grep.txt` via the shared fixture
  `tests/edge/conftest.py::dev_control`.
  Apply the 6.3 signature table: `outcome: LifecycleOutcome` REQUIRED keyword on `_stream_response`, `_stream`,
  `collect_and_render`; `org_id`/`routed_model` on `_next_attempt`, `collect_and_enforce`, `_collect_output_channels`.
  Put `ChatRoute.first_attempt_refusal` and the retry-attempt recheck helper in `edge/incident_recheck.py` (called from
  `routes.py` with one line each). The recheck is placed ABOVE `holds.mark_dispatched()` in `_attempt`/`_next_attempt`
  and `_collect_output_channels`, and before `_stream_response` in `handle()` and before `start_sse` in
  `ResponsesRoute._stream`; stream code mapping kill->`STREAM_KILLED`, stale->`STREAM_SNAPSHOT_STALE`,
  isolated->`STREAM_MODEL_ISOLATED`; non-stream raises module-local `IncidentRecheckRefused`, caught in
  `collect_and_enforce` -> `EnforcedOutput(retry_after_s=...)`. `RouteDeps` gets `control_factory`, `incident_recheck =
  fail_closed_recheck`. `openai_responses.py` passes `outcome=` in `_stream`/`_non_stream` and calls
  `first_attempt_refusal` first in `_stream`; `emit_chat_audit` receives `refused_code`.
  Tests: `test_lgw29_callers_pass_outcome.py` (AST tripwire), `test_lgw29_recheck_refund.py` (refund once with the spent
  cost, `dispatched is False`, zero provider opens, AST source-order test, negative-control variant), `test_lgw29_endpoint_matrix.py`
  (3 endpoints x stream/non-stream x refusal/recheck/cut, plus the clean `/v1/responses` stream and non-stream that must
  complete; route table pins `/v1/completions` -> `chat.handle`), `test_lgw29_cut_bound_property.py` (Property 8a/8b),
  `test_lgw29_inflight_source.py` (C2 `age_s` pin, AC12 refusing default, plan_changed not wired D11), update
  `tests/edge/test_lgw15_stream.py`, `test_lgw15_output.py`, `test_lgw15_exports.py` via
  `tests/edge/_lgw29_support.py::proceed_outcome`.
  Files: `edge/inflight_source.py`, `edge/incident_recheck.py`, `edge/routes.py`, `edge/openai_chat.py`,
  `edge/openai_nonstream.py`, `edge/openai_responses.py`, `edge/app.py`, `edge/serve.py` (docstring), dev profile files, tests.
  Verify: full `pytest -n 24 -q` equals baseline pass count (any newly cut stream = missed migration), `check_sizes gateway_v2`,
  mypy, ruff, `lint-imports`.

- [ ] 15. Other authenticated surfaces: embeddings, moderations, MCP (D§6.4b). Depends on 8, 11, 14. SIZE: openai_misc.py 764.
  Add `MiscRoute.incident_gate = fail_closed_incident`, `incident_org_gate = fail_closed_org`. Put the preamble (decide
  before `_admit`, reroute -> `REROUTE_REFUSED`/`reroute_unsupported`, canonical name into `stage_embeddings`, recheck above
  `holds.mark_dispatched()` in `_drain_vectors`) in a new `edge/incident_misc.py`; `openai_misc.py` adds only call lines
  (<= ~25). Moderations: `decide_org` after identity. MCP: `self.chat.incident_gate.decide_org` in `McpRoute._tenant`
  raising the existing `_Refuse`. `_refuse` paths carry `retry_after_s` and write one audit record.
  Tests: `test_lgw29_misc_surfaces.py` (global/org kill, other org unaffected, unwired refuses, embeddings model kill,
  org-model, isolation -> refuse, case-mutated name, recheck refund, canonical body), AST order test (`decide` before
  `_admit`), and the route-table sweep that classifies every POST route in `build_routes` (covered by `run_lifecycle`,
  covered by 6.4b, or declared placeholder `/v1/rag/query` `/v1/vector/query`; `GET /v1/models*` exempt).
  Files: `edge/openai_misc.py`, `edge/incident_misc.py`, `edge/mcp.py`, test.
  Verify: `pytest -q tests/edge`, `check_sizes gateway_v2`, mypy, ruff.

- [ ] 16. Notifier pump, ticker, catalog loader, constraint source, wiring (`edge/`, D§5.9, 6.5, 4.5, 4.6). Depends on 9, 14.
  Create `edge/notifier_pump.py` (bounded `deque` under lock, drop-oldest + counter, `RerouteLimiter`, drain task with
  `asyncio.wait_for(notify, notifier_timeout_s())`, `LoggingNotifier`, `IncidentTicker` with injected `sleep`),
  `edge/incident_catalog.py` (`AMF_MODEL_CATALOG` JSON, `AMF_MODEL_FALLBACKS`, `AMF_MODEL_AUTO_TARGET`; `ModelCatalog`
  with `digest()`; `ProfileError` on invalid; alias map as `MappingProxyType`; `InterimConstraintSource` with `known=False`
  when no catalog), `edge/incident_wiring.py` (`build_incident(runtime, contract, *, env, clock, wall, rng)` returning
  `IncidentWiring` with gate, control_factory, read_model, pump, risk, isolator, ledger, metrics; `outcome_for(...)` closed
  mapping table of D§5.5 from `GatewayErrorClass`), and a `PassThroughIncidentGate` for dev/test only.
  Tests: `test_lgw29_notifier.py` (raising/blocking notifier never changes the outcome AC28; queue bounded; reroute events
  rate-limited, ISOLATE/RECOVER never; no content/key in events AC30), `test_lgw29_ticker.py`, `test_lgw29_catalog.py`
  (invalid file/target `ProfileError`, digest stable), `test_lgw29_outcome_mapping.py` (table rows incl. 4xx never counted).
  Files: the four new `edge/` modules, tests.
  Verify: `pytest -q tests/edge -k lgw29`, `check_sizes`, `check_no_module_mutable gateway_v2`, mypy, ruff.

- [ ] 17. Production wiring (`edge/production_profile.py`, `state_runtime.py`, `publisher.py`, `series_count.py`, readiness; D§6.5, 5.10). Depends on 16. SIZE: publisher.py 748.
  `edge/state_runtime.py`: build `KillSwitchSnapshot(aliases=...)` from the catalog (single construction site; re-grep),
  `_key_applier` calls `ledger.observe(round_.records)` through a setter like `set_gcra_invalidate`.
  `edge/production_profile.py`: `build_incident` BEFORE `StateRuntime`, bind `gate`, `control_factory`,
  `incident_recheck`, start pump + ticker in the lifespan, register `IncidentMetrics`, replace the "NOT enforced (GW29)"
  log text (lines ~8 and ~492) with the real status line, boot refuses (`ProfileError`) on a `CapacityUnset` incident
  knob, assert the control source is not `DevNoControlSource`. `edge/incident_publish.py` (new) adapts `IncidentMetrics`
  + top-K risk via the GW14d cardinality directory; `publisher.py` and `series_count.py` get only the registration
  lines (new terms `K + OTHER + OVERFLOW`). `/readyz` check that incident wiring is built (and digest non-empty when a
  catalog is set). Log the catalog digest and `KS_SCOPE_GENERATION` at INFO at boot.
  Tests: `test_lgw29_production_wiring.py` (AC12: production control is not `DevNoControlSource`, gate is not the
  fail-closed default), `test_lgw29_series_count_property.py` (Property 10: rendered == declared, invariant to tenant
  count and model count beyond K), extend `tests/edge/test_state_runtime*` for the ledger setter.
  Files: `edge/production_profile.py`, `edge/state_runtime.py`, `edge/publisher.py`, `edge/series_count.py`,
  `edge/incident_publish.py`, `edge/serve.py` (docstring only), tests.
  Verify: `pytest -q tests/edge`, full `pytest -n 24 -q` equals baseline, `check_sizes gateway_v2`, mypy, ruff, `lint-imports`.

- [ ] 18. State-control writer side and rollout guard (`state_control/`, D§7, 9). Independent of 8-17; needs only 2.
  Create `state_control/incident_scopes.py` (org-model projection `om:<slug>:<model>`, writer-side `canonical_model(raw,
  None)` validation -> `invalid_names`, flag `AMF_STATE_PROJECT_INCIDENT_SCOPES` default OFF, ERROR log + refusal to write
  new scopes when ON and `AMF_GATEWAY_MIN_SCOPE_GENERATION` < `KS_SCOPE_GENERATION`); `projector.py` gets only the call
  and drops the "NOT enforced (GW29)" note text for org-model (credential-prefix note stays, minus the GW29 pointer).
  `writer.py`: `StateWriter.isolation(model, *, on, fallback, origin, reason, expires_at, created_at, no_expiry)` via the
  same `put(..., engaged=on)` with explicit OFF, idempotent for an active `origin=auto` record. State_control imports
  only `gateway_v2.domain`.
  Tests: `tests/state_control/test_lgw29_projector.py` (flag ON/OFF matrix, per-key switch still unsupported, explicit OFF
  and `on_count`), `test_lgw29_writer_isolation.py` (idempotent auto, 8x same event -> one record), Postgres-16 case
  gated on `AMF_PG_DSN` with an explicit skip reason, and a gateway-snapshot round trip of a writer-produced record.
  Files: `state_control/incident_scopes.py`, `state_control/projector.py`, `state_control/writer.py`, tests.
  Verify: `pytest -q tests/state_control`, `check_http_outside_edge_resolve state_control`, `check_capacity_literals
  state_control`, mypy, ruff, `lint-imports`.

- [ ] 19. Local equivalents of the live scenarios (D§13), injected clock + fake provider.
  `tests/edge/test_lgw29_l29_1_kill_under_load.py` (includes G-17: 2 000-token stream, engage at +2 s, cut asserted),
  `test_lgw29_l29_2_isolate_reroute.py` (zero calls to M), `test_lgw29_l29_3_auto_isolation.py` (50% 5xx/timeouts across
  `min(floor, active orgs)` orgs trips inside `risk_window_s()`, one-org run trips, two-org run with errors from one does
  not, notification recorded, traffic on the fallback, recovery, no flapping), `test_lgw29_l29_4_expiry.py` (request path
  and read model agree within one refresh). Add `tests/edge/bench_lgw29_hot_path.py` (not collected by pytest; output saved
  in evidence).
  Verify: `pytest -q -k "lgw29 and l29"`.

- [ ] 20. Negative controls (AC32), each reverted afterwards (D§13).
  With a throwaway edit (verify `git diff` is empty after each): remove the phase 4b call; restore `DevNoControlSource` as
  the production default; make the gate treat STALE as OK; make expiry compare against creation only. Each must make the named
  acceptance test fail; record the failing test names in `docs/plans/evidence/2026-11-04-gw29/04_negative_controls.txt`.
  Verify: `git status --short gateway_v2` shows no leftover change.

- [ ] 21. Docs and evidence: `docs/plans/2026-11-04-gw29-killswitch-isolation.md` (plan pointer to this tasks.md),
  `AGENTS.md` GW29 changelog entry (match the R2-xx/GW12 entries: what was wrong, FIX list, OPEN, FOUND NOT FIXED, VERIFY
  with real counts), progress-doc card-table update in `docs/AI_MESH_RUNBOOK_v3.1_PROGRESS.md` stating closed-local vs open
  (live L29-1..L29-4, real notifier delivery, fleet convergence timing, console agreement GW35). Save benchmark output.
  Verify: markdown links resolve (`ls` the referenced evidence files).

- [ ] 22. Final offline gate (container-run, full): run G1, G2, G3, G4 from `gates.sh`; write outputs to
  `docs/plans/evidence/2026-11-04-gw29/02_suite_and_gates.txt` and `03_gate_exits.txt`. Required: 0 failed, no new skips
  beyond the declared Postgres-only tests, `mypy --strict` clean, `ruff` clean, `Contracts: 3 kept, 0 broken`, every
  AST gate exit 0 on both trees (documented `state_control/valkey.py:100` tenant-scale finding excepted), `check_sizes` 0.
  Commit all code, tests and docs locally; do not push.

## Declared OPEN (not closable locally)

Live L29-1..L29-4 on a real stack; real notification delivery; fleet-wide convergence timing of a signed
auto-isolation; console gauge/display agreement in a browser (GW35). No live Docker/staging gate is planned.
