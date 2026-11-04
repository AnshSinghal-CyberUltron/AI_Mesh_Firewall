# Design: GW29 Incident response (kill switch, isolation, reroute, auto-isolation)

**Status:** REVISION 7 (responses to `design-review.md` on revision 6: 1 HIGH, 2 MEDIUM, 2 LOW; recorded in
section 15.7; earlier rounds are 15.6 down to 15.1, newest first; this revision is the authoritative one).
Authored unattended; every ambiguity is resolved by a recorded
conservative fail-closed decision (section 12), none is left open.
**Tree:** worktree `.worktrees/gw29`, branch `gw29-killswitch-isolation`. Source root
`gateway_v2/gateway_v2/`; control-side writer in `gateway_v2/state_control/`.
**Requirements:** `requirements.md` in this directory (R1..R7, N1..N11, AC1..AC32, D1..D13).
**Card:** GW29, V-04 (Blocking). Depends on GW05b, GW06, GW28 (GW28 is NOT started, see 12.C9).

## 1. Overview

Everything GW29 needs to read already exists in RAM: `StateRuntime.switches` is a
`KillSwitchSnapshot` fed from the signed `StateKind.KS` feed, freshness-gated and `on_count`-attested.
Nothing on the request path asks it a question, and the mid-stream seam
(`edge/cancel.py` `InFlightControlSource`) is fed by `_NoControlSource` (`edge/app.py:769`), which
returns `kill_switch_engaged=False` forever. GW29 closes that gap with five pieces:

1. **One synchronous, RAM-only evaluation** on `KillSwitchSnapshot` (`evaluate(org, model)`), used
   by both the admission gate and the per-chunk in-flight source, so the two can never disagree.
2. **An `IncidentGate`** (new, `admit/`) that runs in the chat lifecycle after plan resolution and
   BEFORE the CoDel admission slot and BEFORE the budget spend. It refuses killed/stale traffic,
   applies isolation and reroute through a pure routing function, and returns the routed model.
3. **A per-stream `InFlightControlSource`** built from a `StreamBinding` (org, routed model, key
   hash), replacing `_NoControlSource` as the production default. It cuts at the next chunk on
   kill, revoked key, or stale snapshot (D1).
4. **Two additive scopes in the existing signed KS kind**: `om:<org>:<model>` (org-plus-model kill)
   and `iso:<model>` (isolation carrying fallback, origin, `expires_at`). No new `StateKind`.
5. **A `ModelRiskTracker` + `AutoIsolator`** (new, `admit/`) that scores each model from provider
   errors and output-guard hits, auto-isolates process-locally (tightening only), emits events
   through an injected `Notifier`, and exposes one read model that the request path and every
   display surface share, so expiry cannot diverge (the 23 July defect).

**Technology stack (locked on approval):** Python 3.12, the existing raw-ASGI edge, `dataclasses`
(frozen, slotted), `threading.Lock` for shared structures (same as `KillSwitchSnapshot`),
`asyncio` for the notifier pump only, stdlib `json`/`re`/`collections`. `pytest` with seeded
`random.Random` property tests (no `hypothesis`), `fakeredis` where the store is needed. No new
runtime dependency, no HTTP framework, no new `StateKind`, no Lua. Gates: `mypy --strict`, `ruff`,
`import-linter` (2 contracts), the five AST gates.

### 1.1 Why not the alternatives (decisions taken)

- **New `StateKind.ISOLATION` (fifth kind)** rejected: new manifest, applier, gate, projector path,
  `READY_KINDS` and `series_count` terms. The KS body already is `{"on": bool}` with explicit-OFF and
  `on_count` attestation; isolation is "a model-scoped switch with a payload". Additive optional
  fields cost one parser (D3 holds; demonstration of necessity failed).
- **Isolation as an `ExecutionPlan` field** rejected: plans are per-org and compiled; isolation is
  model-global and must move within one freshness bound without a plan recompile; plan state would
  also put an unsigned-by-incident path through `DispatchRouter`, which must stay pure of identity.
- **Enforcing in `dispatch/routing.py` `DispatchRouter.select`** rejected: its contract (R8.3) is a
  pure function of plan identity, and `ExecutionPlan` has no model field. Model choice happens in the
  lifecycle (`req.model`, `edge/openai_chat.py:625`). The incident decision is a NEW pure function
  in `dispatch/` and `DispatchRouter` is untouched.
- **A middleware in `edge/app.py` before the lifecycle** rejected: the org is only known after
  identity, the model only after parse, and the per-org check must precede quota. The lifecycle is
  the single ordered pipeline (phases 3..7) and already owns refusals and holds.

## 2. Requirement to component map

| Requirement | Component (file) |
|---|---|
| R1 admission consult, order, refusal, audit, counter | `admit/incident_gate.py`, `edge/openai_chat.py` (phase 4b), `edge/incident_wiring.py`, `audit/record.py` cause field |
| R2 mid-stream | `edge/inflight_source.py` (`KillSwitchControlSource`, `InFlightControlFactory`), `edge/cancel.py` (no change to evaluator), `edge/routes.py`, `admit/revocation.py` |
| R3 org-model scope, projector, rollout | `admit/killswitch.py` (additive), `domain/incident.py` (scope grammar), `state_control/projector.py`, `state_control/writer.py` |
| R4 isolation + reroute | `domain/incident.py`, `dispatch/incident_routing.py` (pure), `admit/incident_gate.py` |
| R5 risk, auto-isolation, expiry | `admit/model_risk.py`, `admit/auto_isolation.py`, `admit/incident_state.py` (shared read model), `runtime/resources.py` |
| R6 notification | `domain/incident.py` (`IncidentEvent`, `Notifier`), `edge/notifier_pump.py` |
| R7 metrics, audit | `admit/incident_metrics.py`, `edge/publisher.py`, `edge/series_count.py`, `edge/counters.py`, `edge/errors.py`, `audit/record.py` |
| N1, N2 cost | `KillSwitchSnapshot.evaluate` (one lock acquire), micro-benchmark in evidence |

## 3. Layering and placement (bottom-up import-linter order)

`edge > admit > plan > detect > resolve > dispatch > egress > audit > runtime > contracts > domain`.
`admit` and `dispatch` may import downward only. `state_control` imports only `gateway_v2.domain`.

| Layer | New / changed module | Contents |
|---|---|---|
| domain | `domain/incident.py` (new) | Frozen value types: `IsolationRecord`, `IsolationOrigin`, `ModelStatus`, `ModelProfile`, `RequestConstraints`, `RouteDecision`, `RouteOutcome`, `StreamBinding`, `IncidentEvent`, `IncidentEventKind`; protocols `Notifier`, `FallbackPolicy`, `ModelCatalog`; scope grammar helpers `org_model_scope`, `isolation_scope`, `parse_scope`; `parse_isolation_body` (pure); `canonical_model` (pure, section 4.6); `GateVerdict`, `IncidentDecider` (section 5.4); `ModelOutcome` (section 5.5). |
| domain | `domain/posture.py` (edit) | New codes only: `KILL_SWITCH_ENGAGED`, `MODEL_KILLED`, `MODEL_ISOLATED_NO_FALLBACK`, `REROUTE_REFUSED`, plus the stream code `STREAM_MODEL_ISOLATED` (section 6.4). Added to `__all__`. |
| runtime | `runtime/resources.py` (edit) | All new knobs (section 8). Only module with capacity literals. |
| audit | `audit/record.py` (edit, additive) | A closed `INCIDENT_CAUSES` tuple and an `incident_refusal(...)` constructor that reuses `admission(...)` with `reason=REJECTED` and `cause` in `detail`. |
| dispatch | `dispatch/incident_routing.py` (new) | `decide_route(...)`: pure, no clock, no random, no module state. |
| admit | `admit/killswitch.py` (edit, additive) | Org-model set, isolation map, `evaluate()`, drift latch, `IncidentView`. Same lock, same freshness. |
| admit | `admit/incident_state.py` (new) | `IncidentState`: the ONE read model (signed isolations + process-local auto-isolations + expiry). |
| admit | `admit/incident_gate.py` (new) | `IncidentGate.decide(...)`, `IncidentGate.recheck(...)`. |
| admit | `admit/model_risk.py` (new) | `ModelRiskTracker`: bucketed sliding window, score, probation. |
| admit | `admit/auto_isolation.py` (new) | `AutoIsolator`: threshold crossing to a local isolation + event. |
| admit | `admit/revocation.py` (new) | `RevocationLedger`: bounded key-revoke memory for the in-flight `key_revoked` signal. |
| admit | `admit/incident_metrics.py` (new) | Label-free producer, fixed `SERIES_COUNT`. |
| admit | `admit/__init__.py` (edit) | Export the public surface for `edge`. |
| edge | `edge/inflight_source.py` (new) | Per-stream source and factory. |
| edge | `edge/notifier_pump.py` (new) | Bounded queue, rate limiter, drain task, `IncidentTicker` (5.6), `LoggingNotifier`. |
| edge | `edge/incident_wiring.py` (new) | Builds the incident components from `StateRuntime` + contract; used by `production_profile.py`. |
| edge | `edge/openai_chat.py`, `openai_nonstream.py`, `audit_emit.py`, `chat_lifecycle.py`, `chat_deps.py`, `routes.py`, `app.py`, `serve.py`, `production_profile.py`, `errors.py`, `counters.py`, `publisher.py`, `series_count.py`, `state_runtime.py`, `upstream_body.py` (edits) | Wiring only (section 6). |
| state_control | `projector.py`, `writer.py` (edits) | Org-model scope, flag gate, `StateWriter.isolation(...)`. |

`admit` may import `dispatch` (admit sits above it). `edge/inflight_source.py` imports
`edge/cancel.py` types. `egress` does not change and does not import `detect`.

## 4. Architecture

### 4.1 Request path

```
phase 3 identity  -> Principal(org_id)                       (existing)
phase 4 plan                                                 (existing)
phase 4b INCIDENT GATE   (NEW, RAM only, no await, no store)
   0. model = canonical_model(req.model, catalog aliases)   (4.6); None / unknown -> Refuse MODEL_KILLED
      cause unresolvable_model; `auto` -> AMF_MODEL_AUTO_TARGET or Refuse (unknown_model) (4.6); ALL later steps and the upstream body use this canonical name
   a. KillSwitchSnapshot.evaluate(org_id, model)  -> KillEvaluation   (one lock)
   b. STALE            -> Refuse KILL_SWITCH_UNAVAILABLE      (503)
   c. global / org / org-model engaged -> Refuse KILL_SWITCH_ENGAGED
   d. requested model killed (model:<m>)  -> Refuse MODEL_KILLED
   e. IncidentState.active_isolation(model)   (signed, else local, expiry applied; time from injected wall)
        none        -> route = requested
        isolated    -> decide_route(...) -> Reroute(fallback) | Refuse(...)
   f. every route target is evaluated by (a)-(d) again (fallback killed/isolated => Refuse)
phase 5 admission slot (CoDel)        (existing; never reached when 4b refuses)
phase 6 budget spend / GCRA            (existing; never reached when 4b refuses)
phase 7 input scan, _proceed          (existing; uses routed model, section 6.2)
provider attempt N (run_with_no_splice): IncidentGate.recheck(org, routed_model)  (6.4)
```

A refusal in 4b therefore charges no budget and takes no admission slot (AC5, P9). Order inside 4b
is the order in R1.2: stale, global, org, model. `evaluate` returns all facts under one lock so the
gate classifies in that order without re-locking.

### 4.2 Mid-stream path

`routes._stream` builds `InFlightControl(source=...)` per request (existing). The source is no
longer the shared `self.control` but one built per stream:

```
KillSwitchControlSource(binding).snapshot():
    ev = switches.evaluate(binding.org_id, binding.model)   # one lock, O(1), no I/O
    available = ev.state is not STALE
    kill      = ev.global_engaged or ev.org_engaged or ev.org_model_engaged or ev.model_engaged
    revoked   = ledger.revoked_since(binding.key_hash, binding.key_feed_seq)
    return InFlightSnapshot(available, age_s, kill, revoked, plan_changed=False)
```

`InFlightControl.evaluate` is unchanged: stale first, then max duration, kill, revoke, plan change.
The latch flips, `StreamPipeline` stops forwarding at the next boundary, the provider is aborted by
the existing `CancellationController`, and `_finish_stream` renders `STREAM_KILLED` /
`STREAM_KEY_REVOKED` / `STREAM_SNAPSHOT_STALE` as the terminal error frame. Cut bound = visibility
in RAM (<= `FRESH_MS` after the signed engage) + at most one chunk interval. The two parts are
tested separately (P8): the post-visibility cut must be within `max_cut_latency_s()` of the instant
the snapshot applied the record; `FRESH_MS` belongs only to the end-to-end assertion.

**Age semantics (important, differs from requirements 5.2.2).** `InFlightControl` compares
`snapshot.age_s` to `contract.max_snapshot_age_s()`, which is `target_p99_ms/1000 x 1.0` (about
20 ms). `KillSwitchSnapshot` is allowed to be up to `FRESH_MS = 5 000 ms` old. Passing the real
refresh age would cut EVERY stream. So the source reports `age_s = 0.0` when the snapshot is not
STALE and `available=False` when it is (the snapshot's own 5 000 ms judgement, which already
includes the re-hydrator stamp path). Staleness is decided in exactly one place. A test pins this
(C2).

Isolation does not cut streams (D2). Plan-change is `plan_changed=False` by an explicit named
constant and a docstring plus a test that asserts it is not wired (D11).

### 4.3 State path (signed feed)

Scope grammar in the KS kind (key is the scope; body is JSON with boolean `on`):

| Scope key | Meaning | Extra body fields (all optional, additive) |
|---|---|---|
| `global` | all traffic | none |
| `org:<slug>` | one org | none |
| `model:<name>` | one model, every tenant | none |
| `om:<slug>:<model>` | one org's use of one model (NEW, D4) | none |
| `iso:<model>` | isolation of one model (NEW, D3) | `fallback`, `origin` (`operator`/`auto`), `reason`, `created_at`, `expires_at` |

`<slug>` is `[-a-zA-Z0-9_]{1,64}` (the projector's `_SLUG`), so it contains no `:`; the model is
everything after the second `:` and may contain `:` or `/`. Model names are matched by
`MODEL_NAME = [A-Za-z0-9._:/-]{1,128}`; a name outside it makes the record UNAVAILABLE (existing
unknown-scope behaviour: fail closed, raised as `StoreDataUnavailable`).

`KillSwitchSnapshot` additions (all under the existing `self._lock`):

- `_org_models: set[tuple[str, str]]` and `_isolations: dict[str, IsolationRecord]` next to the existing
  sets. Both are bounded by the number of ENGAGED scopes, as the existing sets are (C36 explicit OFF
  records remove entries; absence never means off).
- `_set_scope` becomes `_apply_record_locked(record)` which parses the scope with
  `domain.incident.parse_scope`, reads `on` via the unchanged `scope_is_on`, and for `iso:` calls
  `parse_isolation_body`. `on_count` accounting: `held = len(orgs)+len(models)+len(org_models)+len(isolations)+global`.
  Isolations count toward `on_count` while their record says `on:true`, whether or not
  `expires_at` has passed. Expiry is evaluated at READ, never by mutating the set, so the
  attestation never depends on a clock.
- **Drift latch (hardening found while reading).** Today a drift mismatch raises
  `StoreDataUnavailable` but leaves `_refreshed_at` untouched, so `state()` stays OK for up to 5 s.
  GW29 sets `_refreshed_at = None` before raising, so drift is STALE immediately (AC4). A later
  clean round sets it again. Additive, fail-closed.
- `evaluate(org_id, model, now=None) -> KillEvaluation` (frozen, slotted): `state`, `global_engaged`,
  `org_engaged`, `org_model_engaged`, `model_engaged`, `refresh_age_s`. One lock acquisition, then
  the stamp check outside the lock as in `state()`.
- `isolation_record(model) -> IsolationRecord | None` (raw, un-expired) and `view()` extended with
  `org_models` and `isolations` counts (names stay out of metrics).

A pre-GW29 gateway seeing `om:`/`iso:` raises on the unknown scope (AC15); the rollout rule is
section 9.

### 4.4 Isolation, expiry and the one read model

`IncidentState` (admit) owns two sources and ONE function that applies expiry:

- signed isolations from `KillSwitchSnapshot.isolation_record(model)`;
- process-local auto-isolations in a lock-guarded `dict[str, IsolationRecord]` bounded by
  `contract.local_isolation_slots()` (injected instance state, no module state).

`IncidentState.active_isolation(model) -> IsolationRecord | None` (the one signature; wall and
monotonic time come from the injected `wall`/`clock`, never parameters) is the ONLY expiry decision in
the code base. Both the request path (`IncidentGate`) and the read model for display
(`IncidentState.snapshot_models(now)` for metrics / a read endpoint) call it, so they agree by
construction (AC25, P6). Rules:

- explicit `expires_at` (finite float, epoch seconds) in the past: inactive, evaluated every read.
- `origin == auto` and `expires_at` missing/non-numeric/NaN/inf/<=0: inactive after
  `created_at + auto_isolation_duration_s()`; if `created_at` is also missing, treated as created
  when first seen by this process (monotonic) and inactive after the same duration (D10).
- `origin == auto` additionally capped by a local monotonic first-seen age
  `<= max_isolation_hold_s()`, so a skewed clock or an absurd `expires_at` cannot hold a model
  isolated indefinitely.
- **Missing `origin` is `operator`** (one rule, review r7 #5); the operator rules below then apply unchanged,
  so an operator record with a finite `expires_at` and no `origin` field is an ordinary expiring operator
  isolation, and one with neither `expires_at` nor `no_expiry: true` is malformed. A PRESENT `origin` that is not
  `operator`/`auto` is malformed.
- `origin == operator` with the explicit field `"expires_at": null` and an explicit
  `"no_expiry": true`: active until the signed OFF. Missing `expires_at` without `no_expiry` on an
  operator record: **malformed** (next bullet).
- malformed isolation extras (non-string `fallback`, bad `origin`, non-finite number, fallback not
  matching `MODEL_NAME`): the record parses to `IsolationRecord(malformed=True)`. The gate treats a
  malformed isolation as `MODEL_ISOLATED_NO_FALLBACK` for that model only (refuse that model's
  traffic, never global, never "inactive", never "forever active"). Malformed `on` still raises
  `StoreDataUnavailable`. Hardening (review r5 #3): `_absorb_locked` clears `_refreshed_at` on EVERY
  `StoreDataUnavailable` it raises (drift, unknown scope shape, malformed `on`), so the snapshot is
  not OK afterwards (global fail closed immediately, not after `FRESH_MS`); a half-applied batch is
  therefore never served as fresh.

**Operator OFF clears local auto state (C10 revised).** When a signed `iso:<m>` record is applied
with `on:false`, `IncidentState` drops the process-local auto entry for `m` on its next read or write
(removal of auto state only; it never touches a kill, so the tightening-only property holds). An
operator who writes OFF therefore gets relief within one refresh, not up to
`auto_isolation_duration_s()`. The risk window for `m` is also reset (probation) so the same stale
samples cannot re-trip it immediately.

**Clock skew for `expires_at`.** Expiry is evaluated as `wall() >= expires_at` with no grace.
Operator records rely on host NTP. The `max_isolation_hold_s()` cap bounds skew impact for
auto-origin records only; an operator record with a far-future `expires_at` is by definition the
operator's choice.

Wall time for `expires_at` is an injected `wall: Callable[[], float]` (production `time.time`);
monotonic time for staleness stays the existing injected `clock`. Neither is read from a module
global.

### 4.5 Reroute: a pure decision

`dispatch/incident_routing.py`:

```python
def decide_route(
    requested: str,
    requested_status: ModelStatus,         # killed? isolation (active, un-malformed)?
    fallback_status: ModelStatus | None,   # None when no fallback named
    requested_profile: ModelProfile | None,
    fallback_profile: ModelProfile | None,
    constraints: RequestConstraints,
) -> RouteDecision: ...
```

No clock, no random, no I/O, no module state (R8.3 unchanged). The impure reads happen in the
caller. Result is one of `RouteOutcome.DIRECT | REROUTE | REFUSE_KILLED | REFUSE_NO_FALLBACK |
REFUSE_REROUTE`, plus `routed_model`, `isolated_model`, `fallback`, `decision_source`
(`"isolation"` on reroute), and a closed `reason` string.

Eligibility of a fallback, all must hold, otherwise `REFUSE_REROUTE` (distinct code
`REROUTE_REFUSED`), never a second hop:

1. fallback is named and differs from the isolated model;
2. fallback is not killed at any scope (global, org, org-model, model) for this org;
3. fallback is not itself actively isolated (single hop, D6);
4. `constraints.allowed_models is None or fallback in allowed_models`;
5. `constraints.known` is true and both profiles exist (an unverifiable constraint is a refusal);
6. `fallback_profile.compliance_tags >= requested_profile.compliance_tags | constraints.required_tags`
   (a reroute never weakens the compliance posture; "inherit at least" rule, GW28-independent);
7. `fallback_profile.sensitivity_ceiling >= max(requested_profile.sensitivity_ceiling, constraints.sensitivity_rank)`.

**Constraints source until GW28 lands (D7/C9).** `edge/incident_wiring.py` provides
`InterimConstraintSource`: `known` is true only when a `ModelCatalog` is configured
(`AMF_MODEL_CATALOG` points at a JSON file validated at boot: name, provider, tags, sensitivity
ceiling; invalid file refuses to start); `allowed_models` is None; `required_tags` empty;
`sensitivity_rank` 0. With no catalog configured, `known=False` and every reroute is a declared
`REROUTE_REFUSED`. That is the safe default: GW29 never reroutes on a guess. The seam is the
`ConstraintSource` protocol (`for_request(plan, org_id, requested_model) -> RequestConstraints`);
GW28 replaces the interim implementation without touching the gate. Handoff recorded (section 11).

### 4.6 Model identity: one canonical name on every side (review finding 1)

Model kills, org-model kills and isolations are matched by exact set membership, so the name MUST
be normalised identically by the writer, the parser and the gate, or a case/alias variant bypasses
a kill. `domain/incident.py` owns the single pure function:

```python
def canonical_model(raw: str, aliases: Mapping[str, str] | None = None) -> str | None:
    # 1. reject non-str, empty after strip, len > MODEL_NAME_MAX, NUL/control chars -> None
    # 2. name = raw.strip().casefold()          (NFKC first; non-ASCII after NFKC -> None)
    # 3. name = aliases.get(name, name)          (single hop; alias targets are already canonical)
    # 4. name must fullmatch MODEL_NAME_CANON (compiled from MODEL_NAME_MAX) else None
```

Rules, all fail-closed:

- **Writer canonicalises WITHOUT aliases** (`canonical_model(raw, None)`: charset, NFKC, casefold,
  length only). `state_control` may import only `gateway_v2.domain` and has no catalog, so it never
  needs one. A record is therefore valid for any gateway alias table. The **gateway applies aliases at
  read**: `parse_scope(key, aliases)` canonicalises the model segment with the gateway's alias map,
  so a stored `model:GPT-4o`, a stored `model:gpt-4o` and a stored dated alias of the same family are
  the same engaged scope on that gateway. The writer additionally validates before any write: a
  model that does not canonicalise is rejected at the writer with `invalid_names` (never written).
- **Alias map reaches the snapshot by constructor.** `KillSwitchSnapshot.__init__` gains
  `aliases: Mapping[str, str] = MappingProxyType({})` (empty default keeps every existing construction
  compiling and equals "no catalog"). `edge/state_runtime.py:183` (the only construction site, verified
  by grep) passes the alias map from the `ModelCatalog` built by `edge/incident_wiring.py`, which is
  built BEFORE `StateRuntime` in `production_profile.apply_production`. The gate, the snapshot parser
  and `IncidentState` all receive the same immutable `MappingProxyType` instance.
- **Per-record quarantine, not a fleet-wide 503 (decision C19).** A KS record whose scope SHAPE is
  known (`model:`/`om:`/`iso:`) but whose model segment fails `canonical_model(segment, aliases)`
  is QUARANTINED, not raised: it can never match an admitted request (every admitted request carries a
  name that passed the same function, so a name that cannot canonicalise cannot be requested), so
  ignoring it cannot admit a killed model. The scope key goes into `_quarantined: set[str]` (bounded
  by engaged scopes), counts toward `held` for `on_count` attestation (so a quarantined ON record does
  not cause drift), increments `incident_quarantined_scopes` (gauge, 5.10) and logs ERROR once per
  rate window with the key length and a hash, never the raw key. A quarantined `iso:` is never
  active. Unknown scope SHAPES (no recognised prefix, bad slug, non-bool `on`) still raise
  `StoreDataUnavailable` (global fail closed, existing behaviour). Because the writer validates
  first, quarantine only fires for hand-edited rows or a bad catalog alias; both are operator-visible
  through the gauge.
- **Catalog identity is visible.** `ModelCatalog` exposes `digest() -> str` (sha256 hex of the
  canonical JSON of the catalog and alias map). `incident_wiring` logs it at INFO at boot with the
  `KS_SCOPE_GENERATION`, and `/readyz` (via `make_metrics_readiness`) adds a check that the incident
  wiring is built and, when `AMF_MODEL_CATALOG` is set, that the digest is non-empty. Gateways with
  different catalog files hold different alias sets for the same record; this is declared and made
  detectable by comparing the logged digests, not silently reconciled.
- The alias table is the `ModelCatalog` alias map (dated snapshots and provider aliases map to the
  canonical family name); it is loaded once at boot from `AMF_MODEL_CATALOG` and passed as an
  immutable `MappingProxyType` (wiring above). The gateway has no other alias source (`dispatch/catalogue.py` holds
  provider entries, not model aliases), so this is declared interim until GW28.
- **Absent model and the `auto` routing sentinel (review finding 5).** `ROUTING_SENTINEL = "auto"` is
  a declared constant in `domain/incident.py` (the product's routing mode, not a model). `"auto"`
  satisfies `MODEL_NAME_CANON`, so `canonical_model("auto")` returns `"auto"` (not `None`; the
  earlier sentence listing "auto" among the `None` cases was wrong and is removed). The gate treats it
  explicitly, before step a:
  (a) NO catalog configured: `auto` and an absent/empty model are REFUSED with `MODEL_KILLED`, cause
  `unresolvable_model` (absent/empty) or `unknown_model` (`auto`), because GW29 has no routing
  component to resolve a concrete model and must not run the kill/isolation checks against a name
  that is not what the provider will serve. This is the conservative fail-closed choice (C24).
  (b) catalog configured: `auto` is refused the same way (`unknown_model`) unless `AMF_MODEL_AUTO_TARGET`
  names a catalog model, in which case the gate substitutes that canonical model for `auto` BEFORE
  step a, so every check (kill, org-model, isolation, reroute) and the upstream body use the concrete
  model. `AMF_MODEL_AUTO_TARGET` unset means refuse; invalid or not in the catalog is a `ProfileError`
  at boot. The substitution is a pure lookup in `ModelCatalog` (no routing logic, no new component);
  full auto-routing remains GW28/GW33. An absent model is never defaulted.
  The gate refuses `canonical_model(req.model) is None` (empty, over-long, non-ASCII after NFKC,
  control chars, bad charset) with `MODEL_KILLED`, cause `unresolvable_model`. When a catalog is
  configured, a canonical name that is not in the catalog is also refused (cause `unknown_model`);
  with NO catalog configured a canonical name other than the sentinel is passed through (kill and
  isolation still match it), and that is the only pass-through case.
- The model that goes upstream is the canonical name that was checked (the reroute fallback is
  canonical by construction). `with_openai_body` is always called with `model_override=routed`
  (not only on reroute) so what was checked is what is sent (6.2). The client's original string is
  never used after step 0 and never as a metric label.
- A model name resolved upstream (provider side) to something different from the name we sent is
  outside what the gateway can know; the declared limit is that kills match the canonical name and
  its catalog aliases only.
- **Alias kill is a family kill (review r5 #5).** Because aliases are mapped to the canonical name at
  read, a signed `model:<dated-alias>` kill kills its whole canonical model (all aliases). This is
  fail-closed and declared; P13 asserts it.

## 5. Components

### 5.1 `domain/incident.py` (new)

All frozen dataclasses use `@dataclass(frozen=True, slots=True)`:

```python
class IsolationOrigin(StrEnum): OPERATOR = "operator"; AUTO = "auto"

@dataclass(frozen=True, slots=True)
class IsolationRecord:
    model: str
    fallback: str | None
    origin: IsolationOrigin
    reason: str                # closed vocab or short operator text, never request content
    created_at: float | None   # epoch seconds
    expires_at: float | None
    no_expiry: bool
    feed_seq: int
    malformed: bool

@dataclass(frozen=True, slots=True)
class ModelStatus:
    model: str
    killed: bool
    isolation: IsolationRecord | None       # ACTIVE only (expiry already applied)

@dataclass(frozen=True, slots=True)
class ModelProfile:  name; provider; compliance_tags: frozenset[str]; sensitivity_ceiling: int
@dataclass(frozen=True, slots=True)
class RequestConstraints: known: bool; allowed_models: frozenset[str] | None; required_tags: frozenset[str]; sensitivity_rank: int
@dataclass(frozen=True, slots=True)
class RouteDecision: outcome; routed_model: str | None; isolated_model: str | None; fallback: str | None; decision_source: str; reason: str
@dataclass(frozen=True, slots=True)
class StreamBinding: org_id: str; model: str; key_hash: str; key_feed_seq: int
@dataclass(frozen=True, slots=True)
class IncidentEvent: kind; scope: str ("global" | org id); model; fallback; reason; origin; recorded_at: float; dedup_key: str
#   dedup_key = f"{model}:{origin}" for ISOLATE/RECOVER (review finding 2); notifier contract is at-least-once

class Notifier(Protocol):         async def notify(self, event: IncidentEvent) -> None
class FallbackPolicy(Protocol):   def fallback_for(self, model: str) -> str | None
class ModelCatalog(Protocol):     def profile(self, model: str) -> ModelProfile | None
```

`dedup_key` for auto-origin ISOLATE/RECOVER is `(model, origin=auto)` within a window of
`auto_isolation_duration_s()`; consumers and control MUST treat events with the same key in the window as one.
`IncidentEvent` has no content, key, credential, request id field. `reason` values for
auto-origin events are drawn from the closed tuple `AUTO_REASONS` (`error_rate`, `guard_rate`,
`probation_failed`); operator reason text is passed through only at the control write and is
truncated and charset-checked at parse (`[ -~]{0,200}`; otherwise `malformed`).

**Length limits are named module constants, not inline literals** (the keyword-based capacity gate
does not catch bare lengths, so the rule is enforced by review and by a unit test that greps the
module for digit runs outside the constants block): `MODEL_NAME_MAX: Final = 128`,
`SLUG_MAX: Final = 64`, `REASON_MAX: Final = 200`, each with a comment saying it is a PROTOCOL
limit of the scope grammar (not a capacity), so it is allowed in `domain/` by the same reasoning as
the existing `_SLUG` in the projector. The regexes are compiled from these constants at import
(compiled `re.Pattern` constants are permitted by the `no_module_mutable` gate).

Pure helpers: `canonical_model` (4.6), `parse_scope(key) -> ParsedScope` (raises `StoreDataUnavailable` on unknown),
`org_model_scope(org, model)`, `isolation_scope(model)`, `parse_isolation_body(record, body) -> IsolationRecord`.

### 5.2 `admit/killswitch.py` (additive edit)

Section 4.3. `KillSwitchState`, `FRESH_MS`, `scope_is_on`, `killswitch_applier`, `killswitch_adopter`
keep their signatures. `engaged_scopes` still works (it already returns every `on:true` scope).
`KillSwitchView` gains `org_models: int` and `isolations: int` with defaults, so existing
constructions and `observe_killswitch` keep compiling; `count` includes them.

### 5.3 `admit/incident_state.py` (new): `IncidentState`

```python
class IncidentState:
    __slots__ = ("_switches", "_wall", "_clock", "_lock", "_local", "_first_seen", "_was_active", "_slots")
    def __init__(self, *, switches, wall, clock, local_slots: int) -> None
    def status_of(self, org_id: str, model: str, ev: KillEvaluation) -> ModelStatus
    def active_isolation(self, model: str) -> IsolationRecord | None
    def sweep(self) -> SweepResult          # frozen: local_expired, signed_expired (disjoint), each model ONCE
    def add_local(self, rec: IsolationRecord) -> bool       # tightening only; False if suppressed
    def snapshot_models(self) -> tuple[IsolationView, ...]  # display/read model, same expiry fn
```

**`sweep()` and `_first_seen` lifecycle (review finding 7).** `sweep()` walks the union of signed
isolations (from `switches`) and local entries, evaluates `active_isolation` for each, and compares
with `_was_active: set[str]` (models active at the previous sweep). It returns, sorted and split into `local_expired` and `signed_expired`, the models
that were active at the previous sweep and are inactive now (expired or cleared), exactly once each,
then updates `_was_active`. It also purges `_first_seen` entries whose model is neither in the signed
isolation set nor in the local table. `_first_seen` is therefore bounded by (signed isolations +
`local_isolation_slots()`), `_was_active` by the same, and neither grows with traffic. Request-path
reads (`active_isolation`) never mutate `_was_active`, so the request path and the display read
model cannot disagree on expiry and the RECOVER event is independent of whether any request for that
model arrives.

**Lock order (review finding 6).** Two locks exist: `KillSwitchSnapshot._lock` (S) and
`IncidentState._lock` (I). They are NEVER nested. `IncidentState` first calls `switches` accessors
(`isolation_record`, `isolation_models()`, which take and release S and return immutable copies),
then takes I to read/mutate `_local`, `_first_seen`, `_was_active`. The snapshot never calls into
`IncidentState`, so `_absorb_locked` (holds S) cannot wait on I and `sweep()` (holds I only after
releasing S) cannot wait on S while holding I. A test (`test_lgw29_lock_order.py`) runs `sweep()`,
`active_isolation` and `_absorb` concurrently under a 2 s watchdog and an AST check that no `with
self._lock` body in `incident_state.py` calls a `switches.*` method.

**Who emits signed-record transitions (review finding 3).** Rule: gateways emit `ISOLATE` and
`RECOVER` ONLY for process-local auto isolations (those in `_local`). Transitions of SIGNED records
(operator or persisted-auto) are not emitted by any gateway: every worker and replica holds the same
record, so a gateway emit would be N events for one transition and would break AC27 "exactly one
event". Control owns the single event and audit record for signed transitions (it wrote the record
and the OFF). `sweep()` therefore returns two disjoint tuples in a frozen `SweepResult(local_expired,
signed_expired)`; the `AutoIsolator` emits `RECOVER` and calls `risk.reset_for_recovery` for
`local_expired` only; `signed_expired` feeds the `incident_isolated_models` gauge and probation reset
of the local risk window (so a recovering model gets hysteresis on every worker) but emits nothing.
When a signed record supersedes a local entry (convergence, 5.6) the local entry is dropped
silently, with no RECOVER. Test: N=8 `IncidentState` instances over one shared signed record that
expires; the total events emitted by the gateway side is 0, and the same N instances over one local
isolation each emit exactly one RECOVER per process (the AC27 "one" is per detecting process for
local auto isolation, and one in control for signed).
`add_local` refuses (returns False, counts) when an ACTIVE operator-origin isolation or an engaged
`model:` kill exists for that model (auto-logic never overrides, shortens or clears: AC24), and
never removes anything. At `local_slots` it evicts the entry with the earliest expiry and counts
`local_isolation_evicted` (C7). Entries expire by the same `active_isolation` rule and are purged
lazily on write.

### 5.4 `admit/incident_gate.py` (new): `IncidentGate`

```python
@dataclass(frozen=True, slots=True)
class GateVerdict:                       # domain/incident.py; the ONLY verdict shape
    refuse_code: str | None              # a domain.posture code, or None when proceeding
    cause: str | None                    # member of INCIDENT_CAUSES when refusing
    retry_after_s: float | None          # >= MIN_RETRY_AFTER_S + jitter when refusing
    routed_model: str | None             # canonical model to send upstream when proceeding
    reroute: RouteDecision | None        # set only when routed_model != the requested canonical name
    def __post_init__(self) -> None: ...  # exactly-one invariant, see below

class IncidentDecider(Protocol):         # domain/incident.py; what ChatDeps types against
    def decide(self, *, org_id: str, requested_model: str, request_id: str) -> GateVerdict: ...
    def recheck(self, *, org_id: str, model: str) -> GateVerdict: ...
    def decide_org(self, *, org_id: str, request_id: str) -> GateVerdict: ...
    # decide_org: global + org scopes + snapshot freshness ONLY (no model, no isolation, no reroute),
    # for surfaces that name no routable model (moderations, MCP; 6.4b). Same GateVerdict shape.

def fail_closed_org(*, org_id: str, request_id: str) -> GateVerdict: ...
# always refuse(KILL_SWITCH_UNAVAILABLE, cause="unwired"); default for the misc/MCP deps

def fail_closed_incident(*, org_id: str, requested_model: str, request_id: str) -> GateVerdict: ...
# always returns GateVerdict.refuse(KILL_SWITCH_UNAVAILABLE, cause="unwired")
def fail_closed_recheck(*, org_id: str, model: str) -> GateVerdict: ...
# same refusal, cause="unwired"; default for RouteDeps.incident_recheck and the non-stream deps

class IncidentGate:                      # admit/incident_gate.py, implements IncidentDecider
    def __init__(self, *, switches, state, catalog, aliases, fallbacks, constraints, contract,
                 metrics, emit: Callable[[IncidentEvent], None], rng: random.Random)
```

`GateVerdict.__post_init__` enforces: exactly one of `refuse_code` / `routed_model` is non-None;
when refusing, `cause` and `retry_after_s` are non-None and `reroute` is None; when proceeding,
`cause` and `retry_after_s` are None; `routed_model` is non-empty. A violation raises
`ValueError` at construction (a programming bug caught in tests, never reachable from input).
Constructors `GateVerdict.proceed(routed_model, reroute=None)` and
`GateVerdict.refuse(code, cause, retry_after_s)` are the only call sites. Callers read
`verdict.refuse_code is not None` to branch (6.1). `should_retry` is not a field: it is the constant
`x-should-retry: false` rendered by `edge/errors.py` for every incident code.

Synchronous and allocation-light (N1): one `evaluate` for the requested model; a second `evaluate`
only on the isolated path. Any exception inside `decide` or `recheck` is caught at the single
boundary and returned as `GateVerdict.refuse(KILL_SWITCH_UNAVAILABLE, cause="gate_error", ...)` and
logged at ERROR with exception class and no content (N3). There is no `except Exception: pass`.

`retry_after_s = max(MIN_RETRY_AFTER_S, gap_retry_after_s(rehydrate_period_ms, refresh_ms)) + jitter`
with jitter from the injected `rng` bounded by `contract.incident_retry_jitter_s()`, mirroring
`admit/grant.py`.

`recheck` is called at each provider attempt (6.4): it re-runs canonical-name `evaluate` +
`active_isolation` for the ALREADY-ROUTED model and returns the same `GateVerdict` shape: a
`refuse` with cause `recheck` (and the underlying code: kill, stale or isolated) or a `proceed` with
`routed_model` unchanged. A reroute never re-runs mid-request.

Reroute events: `emit(IncidentEvent(REROUTE,...))` is called on every reroute. `emit` is the pump's
O(1) append, rate-limited there (5.9). The metrics counter increments on every reroute.

### 5.5 `admit/model_risk.py` (new): `ModelRiskTracker`

```python
class ModelOutcome(StrEnum):
    OK, TIMEOUT, SERVER_ERROR, RATE_LIMITED, MALFORMED, GUARD_HIT
class ModelRiskTracker:
    def __init__(self, *, clock, params: RiskParams, max_models: int)
    def observe(self, model: str, outcome: ModelOutcome, org_id: str) -> None  # O(1), lock, never raises
    def score(self, model: str) -> RiskReading                          # RAM, O(buckets)
    def readings(self) -> tuple[RiskReading, ...]                       # O(models), for the gauge
    def reset_for_recovery(self, model: str) -> None                    # probation start
```

`RiskParams` (frozen, built from the contract, never literals): `window_s`, `bucket_s`, `buckets`,
`min_samples`, `probation_min_samples`, `min_distinct_orgs`, `weights: Mapping[ModelOutcome, float]` (a
`MappingProxyType`), `threshold`.

Each model owns a ring of `buckets` counters keyed by `floor(clock()/bucket_s)`; stale buckets are
zeroed lazily on write. `score = 0.0` when `total < min_samples` (or `probation_min_samples` while
on probation), else `min(1.0, sum(weight[o]*count[o]) / total)`. Weights are in `[0, 1]`
(TIMEOUT/SERVER_ERROR/MALFORMED 1.0; RATE_LIMITED and GUARD_HIT lower, from the contract), so the
score is in `[0, 1]` and monotone non-decreasing in the weighted error count at fixed total (AC21,
P7). `max_models` bounds tracked models; beyond it `observe` increments
`risk_untracked_total` and returns (an untrackable model can never auto-isolate; it can still be
killed or operator-isolated).

**Inputs are attacker-influenced, so the mapping is explicit (review finding 2).** A tenant must not
be able to trip a global model isolation with malformed requests or its own bad key. The edge feeds
the tracker through ONE function, `edge/incident_wiring.py::outcome_for(...)`, with this closed table
(status/class from `dispatch/provider_errors.py`'s `GatewayErrorClass`, never from request content):

| Attempt result | `ModelOutcome` | Counted |
|---|---|---|
| Provider 5xx (500, 502, 503, 504, other 5xx) | `SERVER_ERROR` | yes, weight 1.0 |
| Provider timeout (first-byte or inter-chunk, `TimeoutSource`) | `TIMEOUT` | yes, weight 1.0 |
| Malformed upstream response / over-band / response too large / truncated stream | `MALFORMED` | yes, weight 1.0 |
| Provider 429 | `RATE_LIMITED` | yes, weight 0.5 in the score formula and in the distinct-org rule, nothing else; a sustained rate of 70% or more 429s (score >= 0.35) IS a deliberate trip (decision C21) |
| Output-guard `output_blocked` attributed to the routed model | `GUARD_HIT` | yes, weight 0.5 |
| First byte received and stream completed or cut by client | `OK` | yes, weight 0 |
| Provider 400/401/403/404/413/422 (client- or credential-attributable) | none | NO |
| Gateway-side refusals (kill, stale, quota, input block), client disconnect, drain | none | NO |
| Unmapped status | none | NO (declared; unmapped is never guessed into a model fault) |

Rules that follow:

- **One outcome per attempt.** Each provider attempt contributes exactly one `observe` call at its
  terminal point (open failure, first-byte success, or stream terminal). A retried request is
  several attempts and so several outcomes, each against the model it was actually sent to. A
  guard-hit request records `OK` at first byte and `GUARD_HIT` is a SEPARATE terminal sample only
  when the output guard blocks; the total therefore counts attempts plus output-guard terminals, and
  P7 is stated over that total (a guard hit adds one sample with weight 0.5, it never replaces the
  `OK`). The arithmetic is fixed so the threshold analysis is reproducible: errors only trip when
  `sum(weight*count)/total >= threshold`.
- **Only known models are tracked.** `observe` ignores (and counts `risk_untracked_total`) any
  canonical model that is not in the catalog or in the configured fallback map. Garbage model names
  from clients cannot occupy slots; `max_models` then bounds only operator-known models.
- **Distinct-org requirement.** Each bucket also keeps a small bounded set of distinct org ids that
  contributed a weighted (non-OK) sample (hash-truncated, capped at `risk_min_distinct_orgs()`).
  Each bucket also keeps a second bounded set, `orgs_seen`, of hash-truncated org ids that contributed
  ANY sample including OK (same cap). `RiskReading` exposes `distinct_error_orgs` and
  `distinct_orgs_seen`. **Review finding 2:** `AutoIsolator` refuses to trip unless
  `distinct_error_orgs >= min(risk_min_distinct_orgs(), distinct_orgs_seen)` (working default floor
  3, and never below 1). With many active tenants the full floor of 3 applies, so one abusive tenant
  cannot isolate a model for everyone; with 1 or 2 active tenants (single-tenant deployments, a live
  L29-3 run through one org) the requirement shrinks to the number of orgs actually present, so
  auto-isolation still works. Edge case: a single org that is the ONLY active tenant has nothing to
  protect others from, so it may trip alone; with 2 active orgs and errors from one only, the
  requirement is 2 and it does not trip (the other org is healthy, which is evidence the model is
  fine). The caps keep both sets at `risk_min_distinct_orgs()` entries, so `distinct_orgs_seen` is
  `min(true_count, cap)`; because the cap equals the floor the `min()` is exact. The org id is used only to count; it is never stored in an
  event, label or metric.
- Client disconnects and gateway-side refusals are NOT counted (table).
- **Live L29-3 note (review finding 2).** The run may inject through one org; the precondition is
  stated in the procedure: the trip requires `distinct_error_orgs >= min(floor, orgs active in the
  window)`, so a one-org live run auto-isolates and a run with a healthy second org must send
  errors from every active org. Property P14 gains cases for 1 active org (trips), 2 active with
  errors from 1 (no trip), 3+ active with errors from 2 (no trip) and 3+ with errors from 3 (trips).

### 5.6 `admit/auto_isolation.py` (new): `AutoIsolator`

`evaluate(model)` is called from `observe` completion in the edge (after the outcome is recorded);
`sweep()` is called by the `IncidentTicker` (below). Both are cheap (one `score`, O(isolations)).

**`IncidentTicker` (owner of the tick).** `edge/notifier_pump.py` also defines `IncidentTicker`, one
asyncio task per worker started in the SAME lifespan hook as the pump (`production_profile`, 6.5),
sleeping `contract.risk_bucket_s()` (injected `sleep` so tests drive it by hand) and calling
`isolator.sweep()`. A failed sweep increments `incident_ticker_failures_total`, logs WARNING with the
class name only, and the loop continues; the ticker stopping is observable through the gauge
`incident_killswitch_age_seconds` staying fresh (the request path does not depend on the ticker for
enforcement, only for RECOVER events and probation). `AutoIsolator.sweep()` calls
`state.sweep()`, and for each `local_expired` model emits `RECOVER` once and calls
`risk.reset_for_recovery(model)`; for each `signed_expired` model it only calls
`risk.reset_for_recovery(model)` and emits nothing (4.3 lock-order paragraph, 5.3). Trigger: `reading.score >= threshold` and
sample minimum met and `reading.distinct_error_orgs >= min(risk_min_distinct_orgs(), reading.distinct_orgs_seen)` and no active isolation for the model (signed or local) and not suppressed.

On trigger:
1. look up `fallbacks.fallback_for(model)`. If None: no isolation is created (C6), emit
   `AUTO_ISOLATE_SKIPPED` (reason `no_fallback`) and count it. If a fallback exists, run the SAME
   eligibility test `decide_route` applies (rules 2 to 7 of 4.5) for `(model, fallback)` with the
   interim constraints and the CURRENT kill/isolation status of the fallback, by calling
   `decide_route` itself with the requested model treated as isolated: if the outcome is not
   `REROUTE` (fallback killed, itself isolated, constraints unknown, compliance or sensitivity
   weaker), behave as C6: no isolation, emit `AUTO_ISOLATE_SKIPPED` with reason `fallback_ineligible`,
   count it, and do not retry until the next trigger. Auto-isolation therefore can never create the
   outage it exists to avoid (decision C20). Signed operator `iso:` records are NOT subject to this
   pre-check (an operator may isolate deliberately); if their fallback is ineligible the result is
   the declared `REROUTE_REFUSED`, never a loop (single hop, 4.5 rule 3). Fail-closed here means we never turn a degraded model into a
   self-inflicted outage without a destination; security stops remain kill switches.
2. build `IsolationRecord(origin=AUTO, created_at=wall(), expires_at=wall()+auto_isolation_duration_s(), reason=...)`.
3. `state.add_local(rec)`; on True emit `ISOLATE` and `risk.reset_for_recovery` is NOT called yet.
4. on expiry of a LOCAL isolation (reported in `SweepResult.local_expired` exactly once per model,
   driven by the `IncidentTicker`) the isolator emits `RECOVER` and calls
   `risk.reset_for_recovery(model)`: window zeroed, model enters probation (hysteresis, AC26). During
   probation the lower `probation_min_samples` applies, so a still-failing model re-isolates after a
   handful of probe requests instead of a full window, and stale pre-isolation data never counts.
   Probation ends after `min_samples` clean total samples.

The process-local isolation emits an event that control persists as a signed `iso:<model>` record
(D9). **AC27 interpretation (review finding 2, decision C27):** every worker/replica detects independently,
so for auto-isolation AC27 "exactly one event" holds per detecting process at the gateway and exactly one
after dedup at the writer: the notifier is at-least-once with dedup key `(model, origin=auto)` over
`auto_isolation_duration_s()`, and `StateWriter.isolation` MUST be idempotent: for an already-active
`iso:<model>` with `origin=auto` it returns the existing record, writes nothing and emits no second event.
Test: N=8 trackers all tripping on one model produce 8 gateway events with one dedup key and exactly one
signed record at the writer (`tests/admit/test_lgw29_autoisolate_dedup.py`). Deviation recorded in 15.4; the detecting process does not wait for it. When the signed record lands, `active_isolation`
prefers the signed one and the local entry is dropped on the next write (convergence).

### 5.7 `admit/revocation.py` (new): `RevocationLedger`

`IdentityCache.on_delta` evicts a changed key, so "not in cache" cannot distinguish revoked from
rate-changed. The ledger records only REVOCATIONS, fed from the same `StateKind.KEY` delta in
`StateRuntime._key_applier` (edge) for records with `deleted=True`:

```python
class RevocationLedger:
    def observe(self, records: Sequence[SignedRecord]) -> None
    def revoked_since(self, key_hash: str, key_feed_seq: int) -> bool
```

**Sequence basis (review finding 5).** The ledger stores `record.feed_seq` of the deleted KEY
record (the same per-record sequence `admit/identity.py` copies into `Principal.feed_seq`, verified
at `admit/identity.py:63,76`) per revoked hash. The manifest `feed_seq` is NOT used for comparison
and is not a parameter (a manifest value is the batch high-water and would make a revoke look newer
than it is). Both sides of `revoked_since(h, seq)` are therefore the same quantity: the per-record
sequence of the revoke versus the per-record sequence the stream was admitted under.

`floor_feed_seq: int = -1` initially (no overflow yet; a stream with `Principal.feed_seq` 0 is not
cut by an empty ledger), raised only on overflow drops. `revoked_since(h, seq)` is True if the ledger holds a revoke for `h` with `record.feed_seq > seq`,
OR if `seq <= floor_feed_seq`. Re-created key: a later non-deleted record for the same hash with a
higher `record.feed_seq` does NOT erase the revoke entry (the stream admitted under the old
generation has `seq` below the revoke and is still cut); the new principal is admitted with the new
`feed_seq`, which is above the revoke, so it is not cut. Delete-then-re-add-then-delete keeps the
highest revoke `feed_seq`. Bounded by `contract.revocation_ledger_slots()`; entries older than
`max_stream_duration_s() + fresh` are expired (no stream can be older, it would have been cut by max
duration). On overflow the oldest entry is dropped and
`floor_feed_seq = max(floor_feed_seq, dropped.feed_seq)`; a stream admitted at
`key_feed_seq <= floor_feed_seq` can no longer be proven un-revoked, so it is cut as revoked (C5,
fail closed; the only effect is a bounded set of in-flight streams cut during a revoke storm).
`StreamBinding.key_feed_seq` = `Principal.feed_seq` at admission. Property P11 covers revoke,
overflow and re-create sequences.

### 5.8 `edge/inflight_source.py` (new)

```python
class InFlightControlFactory(Protocol):
    def for_stream(self, binding: StreamBinding | None) -> InFlightControlSource: ...

class KillSwitchControlFactory:      # production: builds KillSwitchControlSource(binding)
class RefusingControlSource:         # binding None / unwired: available=False  -> STREAM_SNAPSHOT_STALE cut
class DevNoControlSource:            # named, explicit, dev/test profile ONLY
```

`edge/app.py` `_NoControlSource` is renamed `DevNoControlSource` (moved to this module; a
re-export of the old private name is NOT kept). The `RouteDeps.control` default becomes
`RefusingControlSource()`. `dev_profile.py` passes `DevNoControlSource()` explicitly. A boot test
asserts the production profile's wiring is not a `DevNoControlSource` (AC12).

**Migration (review finding 6).** The default flips from "never cut" to "always cut", so every
caller that omitted `control` must opt in by name. Verified by grep in this worktree: the only
source references to `_NoControlSource` are `edge/app.py:386` (default), `edge/app.py:769` (class)
and a docstring mention at `edge/serve.py:204`. The implementer's first task is
`grep -rn "_NoControlSource\|build_app(" gateway_v2 tests` and:

- every test or dev helper that calls `build_app(` (or constructs `RouteDeps`) WITHOUT `control=`
  and streams to completion is changed to pass `control=DevNoControlSource()` explicitly (a small
  shared fixture `tests/edge/conftest.py::dev_control` avoids repetition);
- tests that exercise the cut path pass a real `KillSwitchControlFactory`;
- `edge/dev_profile.py` and `edge/dev_components.py` pass `DevNoControlSource()`;
- the list of touched call sites is recorded in the evidence file, and the full offline suite must
  match the baseline pass count (any newly cut stream is a missed migration, not a product bug).

### 5.9 `edge/notifier_pump.py` (new)

`NotifierPump(emit, drain)`: `emit(event)` is a synchronous O(1) append to a
`collections.deque(maxlen=contract.incident_event_queue_slots())` under a lock; the append of an
event onto a full deque drops the OLDEST and counts `events_dropped`. A per-(model, fallback)
`RerouteLimiter` suppresses `REROUTE` events within `contract.incident_event_window_s()` using the
injected clock; ISOLATE and RECOVER are never suppressed. A single background task (started from
the app lifespan) drains and calls `notifier.notify(event)` under
`asyncio.wait_for(..., contract.notifier_timeout_s())`; `TimeoutError` or any exception increments
`notifier_failures_total`, logs WARNING with class name only, and is NOT retried. The request path
never awaits the pump (AC28: cost is one lock + append). `LoggingNotifier` writes one structured log
line (kind, scope, model, fallback, reason, origin) and is the shipped default; `RecordingNotifier`
lives in `tests/` support.

### 5.10 Metrics (`admit/incident_metrics.py`, `edge/series_count.py`, `edge/publisher.py`)

`IncidentMetrics` is a label-free producer with a class attribute `SERIES_COUNT`, `series()` and
`snapshot()` in the same shape as `QuotaMetrics`. Series (all real zeros from construction):

- refusals by closed cause, one series each: `refused_total{...}` is NOT used; each cause is its own
  name: `incident_refused_stale_total`, `_global`, `_org`, `_org_model`, `_model`,
  `_isolated_no_fallback`, `_reroute_refused`, `_gate_error`, `_recheck` (9; `_recheck` counts a
  refusal at the provider-open recheck of 6.4, and `_model` also carries `unresolvable_model` /
  `unknown_model` from 4.6);
- in-flight cuts by cause: `incident_cut_killed_total`, `_key_revoked_total`, `_snapshot_stale_total` (3);
- `incident_reroute_total`, `incident_auto_isolate_total`, `incident_auto_isolate_skipped_total`,
  `incident_recover_total`, `incident_isolated_models` (gauge), `incident_local_isolation_evicted_total`,
  `incident_risk_untracked_total`, `incident_events_dropped_total`, `incident_notifier_failures_total`,
  `incident_revocation_floor_cuts_total`, `incident_killswitch_age_seconds` (gauge, `-1` sentinel when
  never loaded, as `state_metrics`) (11);
- `incident_engaged_scopes` (gauge, from `KillSwitchView.count` MINUS signed isolations that
  `active_isolation` reports expired, review finding 7: `IncidentState` exposes
  `expired_signed_count()` and the gauge is `view.count - expired_signed_count()`, so it agrees with
  `incident_isolated_models`; R7.1 "engaged-scope counts"). Control's obligation to write OFF on expiry
  is recorded as a handoff in section 11, but gateway-side counts do not depend on it,
  `incident_quarantined_scopes` (gauge, 4.6), `incident_scope_generation` (gauge, constant
  `KS_SCOPE_GENERATION`, section 9) and `incident_ticker_failures_total` (counter, 5.6) (4);
- per-model risk: top-K via the existing GW14d cardinality directory (`edge/cardinality.py`) with
  `K = contract.metrics_top_k()`, padded to exactly K plus `other` plus overflow, so the series count
  does not depend on the number of models. Model names are operator-configured, not
  tenant-derived, but are still treated as unbounded input for cardinality purposes.

`IncidentMetrics.SERIES_COUNT = 9 (refusals) + 3 (cuts) + 11 (misc) + 4 (gauges and ticker counter) = 27` fixed scalars (the review asked for a recomputed count;
27 supersedes the earlier 23, and the unit test pins `len(series()) == SERIES_COUNT`); the top-K family adds
`K + OTHER_SERIES + OVERFLOW_SERIES` and is accounted in `edge/series_count.py` as new terms, so the
Publisher's rendered-vs-declared assertion keeps holding (AC29, P10). `edge/counters.py`
`POSTURE_CODES` gains the five new codes (`kill_switch_engaged`, `model_killed`, `model_isolated_no_fallback`, `reroute_refused`, `stream_model_isolated`; all HTTP 503 or the stream error frame; `domain/posture.py` is edited and its `__all__` extended), `edge/errors.py` gains their envelopes, and
`RENDERABLE_STATUSES` is unchanged (all are 503; `STATUS_VOCAB` therefore does not grow). The
`rejected{status,reason}` family and `REASON_VOCAB` are NOT extended (extending multiplies series);
the cause lives on the dedicated counters above and in the audit `detail.cause`.

Console gauge read: `GET /internal/incident` is NOT added in GW29 (no console in scope). The 5 s
gauge reads the same data through `/metrics` (`incident_risk_score` top-K) and, for the display
model, `IncidentState.snapshot_models()` is exposed on the `StateRuntime`-adjacent
`IncidentWiring.read_model()` so GW35 can mount it without touching enforcement. Read cost O(models).

### 5.11 Audit coverage (R7.2; review finding 4)

`audit/record.py` gains one additive constructor, `incident_event(...)`, built on the same
`admission(...)` shape (phase `AuditPhase.ADMISSION`, no new phase value, so `AuditPhase` and every
consumer of it are unchanged). The closed `INCIDENT_CAUSES` tuple holds the reason strings. Exactly
one record per event; every record carries `request_id` (or `""` for non-request events), org id
where the event is org-scoped, `detail.cause`, `detail.status`, and `retry_after_ms` for refusals;
no content, key or credential.

| Event | AuditPhase | `reason` / `detail.cause` | Emitter |
|---|---|---|---|
| Phase 4b refusal (stale, global, org, org-model, model, isolated-no-fallback, reroute-refused, gate_error, unresolvable/unknown model) | ADMISSION | `REJECTED` / cause per 10.1 | `edge/audit_emit.py::emit_incident_refusal`, called from `handle()`'s `finally` as the sole writer when `holds.incident_cause` is set (6.4a); `emit_chat_audit` suppresses its generic `_admission` record in that case |
| Recheck refusal at provider open (6.4) | ADMISSION | `REJECTED` / `recheck` (+ `detail.underlying`) | same function |
| In-flight cut (kill, key revoked, snapshot stale) | ADMISSION | `TERMINATED` / `cut_killed`, `cut_key_revoked`, `cut_snapshot_stale` | `edge/audit_emit.py::emit_incident_cut`, called from `_finish_stream` when the terminal code is a `STREAM_*` incident code; REPLACES the generic terminal record (6.4a) |
| Reroute | ADMISSION | `REROUTED` / `reroute` (`detail.from`, `detail.to` model names, which are operator-known, not content) | `emit_incident_reroute`, called by the lifecycle after a successful `proceed` with `reroute` set (one per request) |
| Isolate / recover / auto-isolate-skipped | ADMISSION | `ISOLATION_CHANGE` / `isolate`, `recover`, `auto_skipped` | the `NotifierPump` drain task via an `audit_emit` callable (system events have empty `request_id`) |

`REASON_VOCAB` (the `rejected{status,reason}` family) is not extended (C13): `TERMINATED`,
`REROUTED` and `ISOLATION_CHANGE` are audit reasons only and are added to `audit.record`'s own
closed reason tuple. A failed audit emit never changes the outcome (10.1 last row). P15 asserts one
record per refusal and per cut; the reroute and isolation-change records are unit-tested (one per
event, none duplicated by the recheck).

## 6. Edits to existing edge code

### 6.1 `edge/openai_chat.py` (chat lifecycle)

Insert phase 4b after the plan phase and `holds.note_request(request_id)`, before `self._admit`:

```python
verdict = self._deps.incident_gate.decide(org_id=identity.org_id,
                                          requested_model=req.model, request_id=request_id)
if verdict.refuse_code is not None:
    holds.note_incident_cause(verdict.cause)   # here, so EVERY caller of run_lifecycle (chat,
                                               # completions, responses) gets the single audit record
    return req, _refused(verdict.refuse_code, request_id=request_id,
                         retry_after_s=verdict.retry_after_s, cause=verdict.cause)
routed = verdict.routed_model          # canonical name; == canonical(req.model) unless rerouted
```

`run_lifecycle` is the ONLY place the verdict and the identity are both in scope, so it builds the
binding and hands both to `_proceed` (review r7 #3). The last line of `run_lifecycle` changes from
`return req, self._proceed(plan, req, scan.decision, request_id)` to:

```python
binding = StreamBinding(org_id=identity.org_id, model=verdict.routed_model,
                        key_hash=key_hash,                      # already derived at phase 6
                        feed_seq=identity.principal.feed_seq)   # Principal.feed_seq, admit/identity.py:56
return req, self._proceed(plan, req, scan.decision, request_id, binding=binding, verdict=verdict)
```

and `_proceed` becomes `_proceed(self, plan, req, decision, request_id, *, binding: StreamBinding,
verdict: GateVerdict) -> LifecycleOutcome` (it has no `identity` today, `openai_chat.py:589`; it does not
get one, only the two values it needs). `_proceed` calls `holds.note_reroute(...)`? It has no `holds`
either, so the reroute note is written in `run_lifecycle` right before the `_proceed` call
(`if verdict.reroute is not None: holds.note_reroute(RerouteNote(...))`), still before any provider
contact and for both paths. `_proceed` stamps `binding`, `routed_model=verdict.routed_model` and
`reroute=verdict.reroute` onto the proceed `LifecycleOutcome`. Existing tests that call `_proceed`
or build a `LifecycleOutcome` positionally keep working because the new outcome fields are
defaulted; tests that call `_proceed` directly (grep in the first edge task) add the two keywords.

`ChatDeps.incident_gate` (in `chat_deps.py`) is typed by the `IncidentDecider` protocol of 5.4 with a
fail-closed default `fail_closed_incident` returning `GateVerdict.refuse(KILL_SWITCH_UNAVAILABLE, ...)`, exactly
like `fail_closed_admission`/`fail_closed_budget`. An unwired route therefore refuses; the dev
profile wires an explicit `PassThroughIncidentGate` (named, test/dev only).

`LifecycleOutcome` gains additive fields with safe defaults: `routed_model: str | None = None`,
`reroute: RouteDecision | None = None`, `binding: StreamBinding | None = None`,
`retry_after_s: float | None = None`, `cause: str | None = None` (member of `INCIDENT_CAUSES`; set on
every refusal so the refusal renderer and `emit_incident_refusal` can read it).
`_refused(code, *, request_id, param, retry_after_s, cause)` sets `retry_after_s` and `cause`. The binding is built
in `run_lifecycle` (above) from `identity.org_id`, the routed model, `hashed_bearer_key(scope)` (already derived for
the budget phase) and `identity.principal.feed_seq`.

The org id is `identity.org_id` from the resolved `Principal` only; nothing from headers or body
reaches the gate (AC7).

### 6.2 Routed model into the upstream body

**Code-verified (review r7 #3).** `with_openai_body(upstream_body, req, contract)` has NO model slot: it builds
`body = {"model": req.model, "messages": ..., **req.forwarded}` (`edge/upstream_body.py:72`), and the only
`None` returns are text-slot mismatches. So the wording "rewrite the model slot / a model slot mismatch returns
`None`" is withdrawn. The change is: `with_openai_body(upstream_body, req, contract, *, model: str)`; the
`"model"` entry of the OpenAI body is `model` (not `req.model`); `model` is a REQUIRED keyword so no caller can
forget it (the grep task lists every caller). `_proceed` passes `verdict.routed_model` to BOTH
`GatewayRequest(model=...)` (`openai_chat.py:625`, today `req.model`) and `with_openai_body(..., model=verdict.routed_model)`,
so what phase 4b checked is exactly what the provider-open body carries (4.6). If `routed_model` is empty
`GateVerdict.__post_init__` has already raised, so there is no unchecked fall-through.
The request-id fingerprint (`_request_id`, `openai_chat.py:654`, hashes `req.model`) is deliberately LEFT on the
client string: it is computed BEFORE phase 4b because the gate takes `request_id`, it is only an opaque
tenant-scoped correlation id (never compared with a kill scope or sent upstream), and two case variants
of one model getting different ids is harmless. Test (`tests/edge/test_lgw29_upstream_model.py`):
for a case-mutated and an alias request (`"GPT-4o "`, dated alias) the `model` in the body handed to
`provider.open` equals the canonical routed name, `GatewayRequest.model` equals it, and the request id is
unchanged versus the client-string derivation; a rerouted request carries the fallback name.
**Reroute provenance sink (review finding 4).**
`RequestHolds` gains one additive slotted field and method: `note_reroute(self, rec: RerouteNote)`
with a read property `reroute`, where `RerouteNote` (frozen, slotted, `domain/incident.py`) holds
`isolated_model`, `fallback`, `decision_source` (closed value `"isolation"`), `reason` (closed
code from `RouteDecision`, e.g. `isolated_fallback`) and `origin` (`operator`/`auto`); model names are
canonical, no content. `run_lifecycle` calls `holds.note_reroute(...)` right before `_proceed` (6.1) when
`verdict.reroute` is set, for ALL endpoints and both paths (they share `run_lifecycle`). Consumers: (1) `edge/audit_emit.py`
copies the note into the request audit record `detail` (`routing.decision_source`, `routing.isolated_model`,
`routing.fallback`, `routing.reason`) via the existing `note_*` emitter functions; (2) the streaming
path adds the same four keys to the response routing trace through `LifecycleOutcome.reroute` (already
carried), and the non-streaming path stamps them into the response `x-amf-routing-reason` header
value `isolation:<isolated_model>-><fallback>` (a response header, edge only; the body is untouched);
(3) the chat metrics `incident_reroute_total` increments once per note. File edits:
`edge/request_holds.py` (the new fields `_incident_cause`, `_incident_underlying`, `_reroute` and their methods, added to the
existing `__slots__` tuple at `request_holds.py:77`; the slots test asserts no `__dict__`), `edge/openai_chat.py`
(`run_lifecycle` call), `edge/audit_emit.py` (copy), `edge/openai_nonstream.py` (header),
`edge/routes.py` (stream trace), `domain/incident.py` (`RerouteNote`). Example tests
(`tests/edge/test_lgw29_reroute_provenance.py`): isolate M with fallback F, send one streaming and one
non-streaming request for M, assert `holds.reroute == RerouteNote("m","f","isolation","isolated_fallback","operator")`,
the audit `detail.routing` carries the same four values, the non-stream header equals
`isolation:m->f`, provider.open was called only with `f`, and a non-rerouted request has
`holds.reroute is None` and no routing keys.

### 6.3 `edge/routes.py` and `edge/app.py` / `serve.py`

`RouteDeps` gains `control_factory: InFlightControlFactory | None = None` and
`incident_recheck: Callable[..., GateVerdict] = fail_closed_recheck` (keyword-only call) (default always refuses
with cause `unwired`, matching `fail_closed_admission`). The production profile binds it to
`IncidentGate.recheck` (called as `recheck(org_id=..., model=...)`). `LifecycleOutcome` does NOT carry
the org id today (fields: `plan, destination, body, refuse_code, request_id, refuse_param`); the
org id is taken from `outcome.binding.org_id` (6.1). **Explicit, code-verified signature changes (review
r7 #1; each `outcome` is REQUIRED, never defaulted, so no caller can silently run with no binding).** All
current signatures were read in `edge/routes.py`, `edge/openai_chat.py`, `edge/openai_nonstream.py`:

| Function (today) | New signature |
|---|---|
| `ChatRoute._stream_response(self, send, plan, destination, body, holds=None)` (`routes.py:415`) | `(self, send, plan, destination, body, holds=None, *, outcome: LifecycleOutcome)` |
| `ChatRoute._stream(self, send, encoder, coalescer, plan, destination, body, holds)` | `(..., holds, outcome: LifecycleOutcome)` |
| `ChatRoute._next_attempt(self, offered, latch, sink, coalescer, kill_latch, control, timeouts, plan, destination, body, produced, holds)` (`routes.py:635`) | `(..., holds, *, org_id: str, routed_model: str)` read from `outcome.binding.org_id` / `outcome.routed_model` in `_stream` |
| `ChatSurface.collect_and_render(self, req, plan, destination, body, send, holds=None)` (`openai_chat.py:681`) | `(..., holds=None, *, outcome: LifecycleOutcome)` |
| `collect_and_enforce(deps, destination, body, *, plan=None, holds=None, block_code, scan_failure_code)` (`openai_nonstream.py:122`) | adds required keywords `org_id: str, routed_model: str` |
| `_collect_output_channels(deps, destination, body, holds)` (`openai_nonstream.py:170`) | `(deps, destination, body, holds, org_id: str, routed_model: str)` |

`outcome` is keyword-only after the existing defaulted `holds`, which is valid Python and leaves every positional call
shape intact. If `outcome.binding is None` (a hand-built outcome, an unwired deployment) the callee does NOT guess an
org: `_stream` uses `RefusingControlSource` and the first-attempt gate (6.3a) refuses with cause `unwired`, so the
failure is a refusal, never a missing in-flight kill. The refusal renderer reads
`outcome.cause`. The non-streaming handler gets the same callable through its deps dataclass in
`openai_nonstream.py` (default `fail_closed_recheck`). `_stream` selects:
`source = control_factory.for_stream(outcome.binding) if control_factory else self.control`, where
`self.control` is annotated `InFlightControlSource` (the existing protocol in `edge/cancel.py`) and `RefusingControlSource` and every
factory product implement it, so both branches have the one protocol type under `mypy --strict`.
`app.py` and `serve.py` stop defaulting to `_NoControlSource`; `build_app` takes the factory from
the production profile. The real sites (only `edge/app.py:386` instantiates `_NoControlSource`; class at `:769`; docstring
mention `serve.py:204`) are found by re-grep in the first edge task, not trusted from these line
numbers, and each default is replaced by `RefusingControlSource()`.

### 6.3a Callers of the shared helpers: `/v1/responses` keeps streaming (review r7 #1, HIGH)

`/v1/responses` is NOT a separate pipeline. It reuses the chat lifecycle and the chat output helpers, so the 6.3
signature changes must reach it. Read in `edge/`, the complete caller set is:

| Helper | Callers (grep `-n` over `edge/`, re-run in the first edge task) |
|---|---|
| `ChatSurface.run_lifecycle` | `routes.py:342` (`ChatRoute.handle`, which serves BOTH `POST /v1/chat/completions` and `POST /v1/completions`, `routes.py:786-787`); `openai_responses.py:135` (`ResponsesRoute.handle`, with the path rewritten to the chat path) |
| `ChatRoute._stream_response` | `routes.py:356`; `openai_responses.py:323` (`ResponsesRoute._stream`, passing a filtering `forward` sink, called AFTER its own `start_sse(send)` at `:311`) |
| `ChatSurface.collect_and_render` | `routes.py:358`; `openai_responses.py:261` (`ResponsesRoute._non_stream`, with a `_Capture` sink) |
| `collect_and_enforce` | `openai_chat.py` (`collect_and_render`) only, plus one test helper |
| tests that call the changed helpers directly | `tests/edge/test_lgw15_stream.py:255` (`_stream_response`); `tests/edge/test_lgw15_output.py:263,1023` and `tests/edge/test_lgw15_exports.py:594` (`collect_and_render`). Each gets `outcome=` from a shared `tests/edge/_lgw29_support.py::proceed_outcome(org_id=..., model=...)` builder |

Without a change the Responses stream would run with no binding and `RefusingControlSource` and be cut on the
first chunk; the required keyword `outcome` removes that failure mode. `openai_responses.py` is edited as follows
(no new Responses-only enforcement logic; it calls the same ChatRoute pieces):

- **Pass the outcome.** `_stream` calls `self.chat._stream_response(forward, plan, destination, upstream, holds,
  outcome=outcome)`; `_non_stream` calls `collect_and_render(req, plan, destination, upstream, capture.send, holds,
  outcome=outcome)`. Both already hold `outcome` (their parameter), so no plumbing is added.
- **(a) One incident-aware refusal renderer.** A phase 4b refusal reaches `ResponsesRoute._refuse`, which already
  renders JSON (never SSE) through `_emit_error_code`; it now passes `retry_after_s=outcome.retry_after_s` (the 6.4a
  extension), giving HTTP 503 + `Retry-After` + `x-should-retry: false`. `holds.note_incident_cause` was already
  called inside `run_lifecycle` (6.1), so the existing `finally` in `ResponsesRoute.handle`
  (`emit_chat_audit(self.chat.audit, holds, refused, ...)`) suppresses the generic admission record and
  `emit_incident_refusal` writes the only one (6.4a).
- **(b) First-attempt recheck BEFORE `start_sse`.** The recheck is one method on `ChatRoute`:
  `async def first_attempt_refusal(self, send, outcome, holds) -> str | None`. It calls `self.incident_recheck(
  org_id=outcome.binding.org_id, model=outcome.routed_model)` (a `None` binding is the `unwired` refusal), and on a
  refuse it calls `holds.note_incident_cause("recheck", underlying=code)`, renders the 503 JSON through
  `_emit_error_code(..., retry_after_s=verdict.retry_after_s)` and returns the refuse code; otherwise it returns
  `None` and writes nothing. `ChatRoute.handle` calls it before `_stream_response` (6.4); `ResponsesRoute._stream`
  calls it as its FIRST statement, before `start_sse(send)`. Both keep the returned code in a local
  `refused_code` that replaces `outcome.refuse_code` as the argument of `emit_chat_audit` in their `finally`
  (`ResponsesRoute.handle` today derives `refused` only from `outcome.refuse_code`, which is `None` on the recheck
  path, so without this the observer would not count a refusal).
- **Non-streaming Responses.** The recheck sits in `_collect_output_channels` (6.4), so a refusal arrives in
  `_non_stream` as the captured 503 envelope; `_non_stream` already forwards `capture.status`, body and
  `capture.headers` verbatim when `capture.status != 200`, so `Retry-After` and `x-should-retry` pass through
  unchanged. No Responses edit beyond passing `outcome`.
- **Mid-stream cut.** `ChatRoute._finish_stream` writes the `STREAM_*` error frame through the same `forward` sink;
  `ResponsesEventTranslator` turns a chat error frame into a failed response carrying that code
  (`edge/wire/responses_events.py`, `_error_once`). A kill therefore surfaces as a `response.failed` event with
  `STREAM_KILLED` after at most one more chunk, and the cut audit record (6.4a) is written once because
  `_finish_stream` runs inside the shared `_stream`.
- **(c) Tests per endpoint** (`tests/edge/test_lgw29_endpoint_matrix.py`, parametrised over the cartesian product
  `{/v1/chat/completions, /v1/completions, /v1/responses} x {stream, non-stream}`): phase 4b global/org/model kill
  refusal returns 503, `Retry-After` integer `>= 1`, `x-should-retry: false`, zero provider calls, zero net spend and
  exactly one audit record; first-attempt recheck refusal (engage between phase 4b and the recheck) does the same and
  for the streaming variants writes NO SSE head; mid-stream cut for the streaming variants ends the stream after at
  most one more chunk with the endpoint's own failure shape (SSE error frame for chat/completions, `response.failed`
  for responses) and exactly one terminal audit record; and, as the regression for this finding, a CLEAN (no kill)
  `/v1/responses` stream and non-stream with a wired production-shape gate completes fully (not cut, not refused).
  A route-table assertion pins that `/v1/completions` is bound to `chat.handle`.
- **(d) Caller tripwire.** `tests/edge/test_lgw29_callers_pass_outcome.py` walks the `gateway_v2/edge` AST and
  asserts (i) every call to `_stream_response` / `collect_and_render` passes the `outcome` keyword, (ii) every call to
  `run_lifecycle` is in the allowed set `{ChatRoute.handle, ResponsesRoute.handle}`, so a new caller is a deliberate
  edit of the test, and (iii) `collect_and_enforce` / `_collect_output_channels` calls pass `org_id` and
  `routed_model`. The first edge task also runs the grep for all four names (including `tests/`) and records the hits
  in the task evidence.

### 6.4 Attempt re-check (no isolated model on retries; review finding 4)

There are two call sites in streaming. (1) FIRST attempt: the shared `ChatRoute.first_attempt_refusal` (6.3a)
runs the recheck right before `_stream_response` (`routes.py:356`) in `ChatRoute.handle`, and before `start_sse`
in `ResponsesRoute._stream`, because both paths put the 200 head on the wire before `_stream`; it is still
before any `mark_dispatched`. (Where this section says `handle()` for the first-attempt rule, read "`handle()`
or `ResponsesRoute._stream`, via `first_attempt_refusal`".) (2) RETRY attempts: `_next_attempt`
(`edge/routes.py:635`; `_attempt` opens the provider at `routes.py:691`) is passed
`org_id` and `routed_model` (see 6.3) and calls `deps.incident_recheck(org_id=org_id,
model=routed_model)`. **Placement (review finding 1): BEFORE `holds.mark_dispatched()`, never after it.**
In `_attempt` the call goes immediately above `holds.mark_dispatched()` at `routes.py:689` (so the
order is recheck, then `mark_dispatched`, then `self.provider.open(request)` at `routes.py:691`). In
the non-streaming path it goes at the top of `_collect_output_channels` (`openai_nonstream.py:170`),
immediately above `holds.mark_dispatched()` at line 198, which precedes `deps.provider.open(` at
line 199. `mark_dispatched` says "the spend stands" (C29); a recheck that ran after it would leave a
refused request charged and break P9/AC5. It returns the `GateVerdict` of 5.4. On a `refuse` the
holds are left UNDISPATCHED. On a no-splice RETRY attempt `mark_dispatched` has already been set by
the first attempt (it is an idempotent flag), so a refusal on a later attempt does not refund: the
first attempt really reached the provider and the spend stands, which is correct and declared.
On a `refuse`:

- **Streaming and the HTTP head.** `_stream_response` sends the 200 SSE head before `_stream`, so the
  FIRST-attempt recheck is run in `handle()` before `_stream_response` (call site 1 above). A
  refusal there returns the ordinary 503 JSON envelope (same as phase 4b), with `Retry-After`
  and `x-should-retry: false`, and the holds are undispatched (refund). A recheck refusal on a LATER attempt, or any cut after the head, can
  only be the terminal SSE error frame (the head is already 200); that is the existing `STREAM_*`
  behaviour and is declared, not hidden.
- **Streaming (after the head):** the attempt does not open the provider; the kill latch is flipped with the stream
  code mapped from the underlying verdict code: kill/engaged -> `STREAM_KILLED`; stale ->
  `STREAM_SNAPSHOT_STALE`; model isolated after admission -> the NEW code `STREAM_MODEL_ISOLATED`
  (added to `domain/posture.py`, to `edge/errors.py` as a 503 error frame, and to
  `edge/counters.py`'s posture codes). `_run_attempts` ends byte-less with the declared error frame.
- **Non-streaming (`openai_nonstream.py`, same recheck before its provider open):** the response is
  the SAME 503 envelope as the phase 4b refusals, code from the verdict (`kill_switch_engaged`,
  `model_killed`, `kill_switch_unavailable`, or `model_isolated_no_fallback`), `Retry-After` >= 1 s
  + jitter, `x-should-retry: false`. Mechanism: `_collect_output_channels` raises a small
  `IncidentRecheckRefused(verdict)` (module-local exception, no state) from the recheck site; `collect_and_enforce`
  catches it next to `UpstreamFailed`, calls `holds.note_incident_cause("recheck", underlying=code)` and returns
  `EnforcedOutput(deltas=(), refuse_code=code, retry_after_s=verdict.retry_after_s)` (`EnforcedOutput` gains the
  defaulted field `retry_after_s: float | None = None`); the shared `emit_enforced_chat` passes it to
  `errors.render_http(retry_after_s=...)`, so chat, completions and Responses render identically.
- **Accounting (both):** cause `recheck` is added to `INCIDENT_CAUSES`; counter
  `incident_refused_recheck_total` increments once; exactly ONE audit record is written through
  `incident_refusal` (`detail.cause="recheck"`, `detail.underlying=<code>`). Phase 4b did not write a
  refusal record for this request (it proceeded), so there is no double record. Refund (review
  finding 1): the first-attempt refusal returns with `holds._dispatched == False` and `holds._spent == True`,
  so the existing `RequestHolds.release()` `finally` (`edge/request_holds.py`, "refund iff spent and not
  dispatched") calls `quota.refund(cost, request_id=request_id, owner=org_id)`, which the composed
  `QuotaGate.refund` (`edge/quota_gate.py:117`) forwards to `QuotaComponent.refund(cost, *, request_id,
  owner)` (`admit/quota.py:406`). No new refund code is written; the recheck only must not call
  `mark_dispatched`. Tests (`tests/edge/test_lgw29_recheck_refund.py`): (a) streaming first-attempt (refused in `handle()`, 503 JSON, no SSE head written)
  and (b) non-streaming recheck refusal each assert `holds.dispatched is False`, exactly one
  `refund(cost, request_id=..., owner=org_id)` call with the spent cost, zero `provider.open` calls and
  zero net spend; (c) a source-order test (AST: the `incident_recheck` call precedes
  `holds.mark_dispatched()` in `_attempt`/`_next_attempt` and `_collect_output_channels`, and precedes
  `_stream_response` in `handle()`); (d) the negative control "move the recheck below
  `mark_dispatched`" fails (a).

Because only the first byte closes the no-splice latch, a retry before the first byte after an
isolation lands is refused rather than sent to the isolated model; it is NOT rerouted mid-request
(R4.4, AC20).

### 6.4a Refusal rendering, headers and the single audit record (review round 6)

**Headers (finding 1).** Edits: `edge/errors.py::render_http(code, *, param, request_id, boundary,
retry_after_s: float | None = None)` adds, when `retry_after_s` is not None, `retry-after` = `max(1, ceil(retry_after_s))`
(integer seconds, never below `MIN_RETRY_AFTER_S`) and `x-should-retry: false` to `HTTPResponse.headers`; `retry_after_s=None` leaves every
existing caller byte-identical. `edge/routes.py::_emit_error_code(..., retry_after_s=None)` and
`_render_refusal(send, req, code, request_id, param, holds, *, retry_after_s=None, cause=None)` pass it through; `handle()` calls
them with `outcome.retry_after_s` and `outcome.cause`; the non-streaming handler does the same. Incident
refusals always carry a non-None `retry_after_s` (jittered, `gap_retry_after_s`). Test
`tests/edge/test_lgw29_refusal_headers.py`: both headers present on the phase-4b, first-attempt recheck and
non-streaming refusals; `Retry-After` is an integer `>= MIN_RETRY_AFTER_S`; non-incident refusals unchanged.

**Streaming refusals are HTTP (finding 2).** Today `_render_refusal` turns any post-parse refusal of a
streaming request into HTTP 200 plus an SSE error frame. Rule: refusals whose code is in
`INCIDENT_HTTP_CODES` (`KILL_SWITCH_ENGAGED`, `MODEL_KILLED`, `MODEL_ISOLATED_NO_FALLBACK`, `REROUTE_REFUSED`,
`KILL_SWITCH_UNAVAILABLE`) render as HTTP 503 even when `req.streaming`: `_render_refusal` gets a branch
`if code in INCIDENT_HTTP_CODES: return await _emit_error_code(...)` placed BEFORE the streaming branch. This deliberately
differs from other post-parse refusals, because no SSE head has been sent at this point and an HTTP 503 can carry
`Retry-After`. `INCIDENT_HTTP_CODES` is a frozen tuple in `edge/errors.py`. Test: a streaming request to a killed org
gets status 503, `retry-after`, `x-should-retry: false`, no `content-type: text/event-stream`, and no SSE head is written.

**One audit record, one observer outcome (finding 3).** `RequestHolds` gains `note_incident_cause(cause, underlying=None)` and read
properties `incident_cause` and `incident_underlying` (additive slotted fields). Rules: (a) `emit_chat_audit` suppresses its own ADMISSION `REJECTED` record
(`audit_emit.py`, the `refuse_code` branch) whenever `holds.incident_cause` is set; `emit_incident_refusal` is then the only
writer. (b) Phase 4b refusals call `holds.note_incident_cause(verdict.cause)` inside `run_lifecycle` (6.1), so chat,
completions and Responses all get it. (c) For the first-attempt recheck refusal, `first_attempt_refusal` (6.3a) calls
`holds.note_incident_cause("recheck", underlying=verdict.refuse_code)` and returns the code; the caller
(`ChatRoute.handle` or `ResponsesRoute.handle`) keeps it in a local `refused_code` and passes `refused_code` (not
`outcome.refuse_code`, which is None there) to `emit_chat_audit` in the `finally`, so `observe_outcome` counts a refusal and the generic
input-decision/provider_called=false records are not written. (d) Retry-attempt and non-streaming recheck refusals use the same
note, and because `collect_and_render` returns nothing to its caller, `emit_chat_audit` itself derives the refused code:
when its `refuse_code` argument is `None` and `holds.incident_cause == "recheck"`, it uses `holds.incident_underlying`
(so the non-streaming chat and Responses `finally` blocks need no extra local). Tests (P15, `test_lgw29_recheck_refund.py`, phase-4b test): count ALL audit records over one whole `handle()` call and assert
exactly one (phase `ADMISSION`, `REJECTED`, `detail.cause` as expected), and the observer outcome is "refused" once.

**Cut record (finding 4).** `emit_incident_cut` REPLACES the generic terminal record for a stream ended by a `STREAM_*` incident
code: `note_stream_output`/the streaming `finally` skip the generic terminal record when `holds.incident_cause` is a cut cause
(`cut_killed`, `cut_key_revoked`, `cut_snapshot_stale`, `cut_model_isolated`), and `_finish_stream` calls `holds.note_incident_cause(...)` before
they run. Test: a cut stream produces exactly one terminal audit record.

### 6.4b Endpoints with their own admission path (review r7 #2, MEDIUM): embeddings, moderations, MCP

`routes.py:786-796` mounts `/v1/embeddings` (`EmbeddingsSurface.handle`, `openai_misc.py:304`), `/v1/moderations`
(`ModerationsSurface.handle`, `openai_misc.py:691`) and, via `edge/mcp.py`, the MCP surface. None goes through
`run_lifecycle`. A kill switch that leaves one authenticated endpoint open is a fail-open, so GW29 WIRES them
(option a) rather than declaring a gap. Each gets the gate through its own deps value with the fail-closed default
(no wiring means refusal, like `fail_closed_admission`): `MiscRoute.incident_gate: IncidentDecider =
fail_closed_incident` and `MiscRoute.incident_org_gate: Callable[..., GateVerdict] = fail_closed_org`; MCP reads
`self.chat.incident_gate.decide_org` (it already holds the `ChatRoute`). Org id is `identity.org_id` from the
resolved Principal only (AC7).

| Surface | Where | Check | Refusal rendering |
|---|---|---|---|
| `/v1/embeddings` | in `EmbeddingsSurface.handle` after the plan phase and `request_id`, BEFORE `self._admit` (phase 4) | `incident_gate.decide(org_id, requested_model=req.model, request_id)` (global, org, org-model, model, isolation); the canonical routed name replaces `req.model` in `stage_embeddings` (`embeddings_input.py:168`). **Embeddings never reroute**: vector dimensions differ between models, so a verdict with `reroute` set is converted to a refusal `REROUTE_REFUSED`, cause `reroute_unsupported` (added to `INCIDENT_CAUSES`) | the misc `_refuse(send, code, observer)` path gains `retry_after_s` so the 503 carries `Retry-After` + `x-should-retry: false` (the same `render_http(retry_after_s=...)` of 6.4a), `holds.note_incident_cause(cause)`, one audit record |
| `/v1/embeddings` provider open | immediately above `holds.mark_dispatched()` in `_drain_vectors` (`openai_misc.py:588`), before `provider.open` | `incident_gate.recheck(org_id, model=routed)`; refusal leaves holds undispatched, so the existing `release()` refunds (same rule as 6.4) | same |
| `/v1/moderations` | after the identity phase (`openai_misc.py:720`), before the scan | `incident_org_gate(org_id, request_id)` = `decide_org`: global and org kill plus snapshot freshness. It opens no provider and names no routable model, so model kill and isolation do not apply; an org-wide or global kill still stops the endpoint | `_refuse` with `retry_after_s`; one audit record |
| MCP | in `McpRoute._tenant` right after identity (`mcp.py:210-213`), before the plan read | `self.chat.incident_gate.decide_org(...)`; refusal raises the existing `_Refuse(code)` | the existing MCP error rendering; `Retry-After` is attached where the MCP path already renders HTTP headers, otherwise declared absent (JSON-RPC body carries the code) and the refusal is still one audit record |

Mid-stream cut does not apply to these surfaces (no SSE). Tests (`tests/edge/test_lgw29_misc_surfaces.py`):
for each of embeddings, moderations, MCP: global kill and org kill refuse with the 503 code and zero provider/broker
calls; other orgs unaffected; unwired default refuses (`fail_closed_*`); embeddings: model kill, org-model kill,
isolation-with-fallback (refuses, `reroute_unsupported`, no provider call), case-mutated model name refused, the
recheck refuses between decide and open with zero net spend, and the provider-open body carries the canonical name.
Source-order test (AST): `decide` precedes `_admit` in `EmbeddingsSurface.handle`. A sweep test enumerates every
`POST` route in `build_routes` and asserts each handler is in `{covered by run_lifecycle, covered by 6.4b, declared
placeholder}`; `/v1/rag/query` and `/v1/vector/query` are `_placeholder(...)` (they serve nothing), and
`GET /v1/models*` read no tenant model (declared exempt: they return a catalog list, not a service). A new
route fails the sweep until classified, which keeps the "no open authenticated endpoint" property true.

### 6.5 Production wiring

`edge/incident_wiring.py::build_incident(runtime, contract, *, env, clock, wall, rng)` returns an
`IncidentWiring` with `gate`, `control_factory`, `read_model`, `pump`, `risk`, `isolator`,
`ledger`, `metrics`. `edge/state_runtime.py::_key_applier` additionally calls
`ledger.observe(round_.records)` (ledger injected through a setter like
`set_gcra_invalidate`, no-op until wired, and the production profile asserts it is wired).
`edge/production_profile.py` builds the wiring in `apply_production`, registers `IncidentMetrics`
with the Publisher, starts the pump task in the lifespan, and replaces the "NOT enforced on the
request path (GW29)" log text (lines 8 and 492) with the real status line:
`kill switch -> enforced at admission and per chunk; scopes global,org,model,org-model,isolation`.
Boot refuses to start (`ProfileError`) if any incident knob cannot be derived (`CapacityUnset`).

## 7. State-control (writer side, `state_control/`)

`projector.desired_state` currently sends everything but "org-wide disable, no credential prefix" to
`unsupported`. New logic, gated by `AMF_STATE_PROJECT_INCIDENT_SCOPES` (default OFF, see rollout):

- org + concrete model name + no credential prefix + action disable -> `om:<slug>:<model>` ON
  (model name validated against `MODEL_NAME`; invalid -> `invalid_names` + note, not projected).
- credential-prefix switch -> stays `unsupported_switches += 1` with the note, unchanged (D5).
- flag OFF: org-model switches stay `unsupported` with the existing note (so a not-yet-rolled fleet is
  never handed an unknown scope).
- `_Desired.switches_on` already carries scope strings; `_switches_on` / `_switches_off` already
  handle arbitrary scope keys and write explicit OFF (C36), so `on_count` stays right through
  `StateWriter.killswitch(scope, on=...)`. No change to `pg.py`/`schema.py`.

`StateWriter.isolation(model, *, on, fallback, origin, reason, expires_at, created_at, no_expiry)`
(new, additive) writes `{"on": on, ...}` under `iso:<model>` through the same `put(..., engaged=on)`,
with the same explicit OFF on removal. Control (out of scope) calls it for operator isolation and
for persisting an auto-isolation event. `state_control` imports `gateway_v2.domain.incident` for the
scope helpers and body builder only (allowed direction).

The "NOT enforced by gateway v2 (GW29)" note text is removed for org-model; the credential-prefix note
keeps its text minus the "(GW29)" pointer (it is now a permanent D5 limitation).

## 8. Resource derivations (`runtime/resources.py`, the only home of literals)

New `ResourceContract` methods, each a derivation of an existing field with a named `_MULT`/`_FRACTION`
constant, fail-closed (`CapacityUnavailable`) when it cannot be derived. Working defaults are
OWNER-TO-SIGN (D8); names are final, numbers are the working defaults:

| Method | Derivation (working default) | Used for |
|---|---|---|
| `risk_window_s()` | `_RISK_WINDOW_S = 60` | sliding window |
| `risk_bucket_s()` | `risk_window_s() / _RISK_BUCKETS` (`_RISK_BUCKETS = 12`, so 5 s = the console gauge cadence) | bucket width |
| `risk_min_samples()` | `_RISK_MIN_SAMPLES = 20` | minimum before a score |
| `risk_probation_min_samples()` | `_RISK_PROBATION_MIN = 5` | post-recovery hysteresis |
| `risk_threshold()` | `_RISK_THRESHOLD = 0.35` (50% 5xx/timeouts trips; 10% does not; 50% 429 scores 0.25 and does NOT trip; 100% 429 scores 0.5 and DOES trip) | auto-isolation trigger |
| `risk_weights()` | `MappingProxyType` timeout/5xx/malformed 1.0, rate-limit 0.5, guard hit 0.5 | score |
| `risk_max_models()` | `metrics_directory_slots() / _RISK_MODELS_DIV` | tracker bound |
| `auto_isolation_duration_s()` | `risk_window_s() * _AUTO_ISOLATE_WINDOWS` (5 windows = 300 s) | default `expires_at` |
| `max_isolation_hold_s()` | `auto_isolation_duration_s() * _HOLD_CAP_MULT` (2) | local monotonic cap |
| `risk_min_distinct_orgs()` | `_RISK_MIN_ORGS = 3` | distinct-org floor before auto-isolation |
| `local_isolation_slots()` | `risk_max_models()` | local auto-isolation bound |
| `revocation_ledger_slots()` | `connection_budget() * _REVOKE_SLOTS_MULT` | ledger bound |
| `incident_event_queue_slots()` | `_EVENT_QUEUE_SLOTS` derived from `event_log_rate()` | pump queue |
| `incident_event_window_s()` | `metrics_window_s()` | reroute event rate limit window |
| `notifier_timeout_s()` | `max(p99_s * _NOTIFIER_TIMEOUT_MULT, p99_s)` | pump call bound |
| `incident_retry_jitter_s()` | `MIN_RETRY_AFTER_S * _RETRY_JITTER_FRACTION` | jittered Retry-After |

`snapshot()` is extended to include them. A startup-ordering check
(`_check_incident_ordering`): `threshold in (0,1)`, `probation_min_samples <= min_samples`,
`bucket_s * buckets == window_s`, `auto_isolation_duration_s() >= risk_window_s()`; violation raises
`CapacityUnavailable` at boot. The declared auto-isolation detection window for L29-3 is
`risk_window_s()` (time to collect `min_samples` weighted errors at the offered rate); test asserts
the trip occurs inside it at 50% errors for any offered rate that yields `min_samples` in the window.

## 9. Rollout and compatibility (R3.5, D13)

An old gateway raises `StoreDataUnavailable` on `om:`/`iso:`, which stales its snapshot and returns
503 for all traffic. Therefore the order is mandatory and encoded:

1. Deploy GW29 gateways to 100% of the fleet (they understand all five scope shapes; their behaviour
   with only old scopes is unchanged).
2. Only then set `AMF_STATE_PROJECT_INCIDENT_SCOPES=1` on the projector. Default is OFF.
3. Operator isolation records are written only by control after step 2; control's isolation write
   path checks the same flag.

**Declared limit of the guard.** `AMF_GATEWAY_MIN_SCOPE_GENERATION` is an operator declaration, not a
verified fact: one old pod left running after the flag flips still 503s its own traffic. To make this
checkable rather than trusted, each gateway worker publishes `incident_scope_generation` (gauge,
constant `KS_SCOPE_GENERATION`) in its metrics; the rollout runbook step before step 2 is to confirm
the fleet minimum of that gauge is 2 with the query `min(incident_scope_generation) >= 2` (PromQL,
aggregated across all gateway workers and replicas; an absent series, e.g. an old pod that does not
export it, makes `count(incident_scope_generation) < count(up{job="gateway"})`, so the step also
requires `count(incident_scope_generation) == count(up{job="gateway"})`; the rollout is blocked if
either fails; review finding 9). The projector itself cannot read gateway metrics (separate
process, no coupling added), so the check stays an operator step; this is recorded as decision C22.

Guards: the flag defaults OFF; `state_control` logs at ERROR with the remediation when it is ON and
`AMF_GATEWAY_MIN_SCOPE_GENERATION` (a deploy-time declaration of the fleet's `KS_SCOPE_GENERATION`
constant in `domain/incident.py`, value 2) is unset or lower, and refuses to write the new scopes in
that case. The gateway boot log prints its `KS_SCOPE_GENERATION`. AC15's round-trip test runs an
"old applier" (a copy of the pre-GW29 `_set_scope` behaviour embedded in the test) against a new
record and asserts it raises, and runs the new snapshot against old-only records and asserts parity.

## 10. Error handling and validation

### 10.1 Request path

| Condition | Class | Caller receives | Logged |
|---|---|---|---|
| Snapshot STALE / never loaded / drift latch | recoverable (clears on a clean round) | 503 `service_unavailable`, code `kill_switch_unavailable`, `Retry-After` >= 1 s + jitter, `x-should-retry: false` | WARNING once per rate window, via `edge/event_log.py` (counter always increments) |
| Global / org / org-model engaged | operator-controlled | 503, code `kill_switch_engaged`, same headers | INFO rate-limited; no scope name echoed to the client |
| `model:<m>` engaged for the routed model | operator-controlled | 503, code `model_killed` | same |
| Isolated, no fallback, or malformed isolation | operator-controlled | 503, code `model_isolated_no_fallback` | same |
| Reroute ineligible (killed, isolated, disallowed, constraint or catalog unknown) | recoverable | 503, code `reroute_refused` | same |
| Model name does not canonicalise, or is not in the configured catalog (incl. absent model, or `auto` without `AMF_MODEL_AUTO_TARGET`, 4.6) | client-attributable | 503, code `model_killed` (single posture code, no new status) with the envelope `error.param` / log cause set to `unresolvable_model` or `unknown_model`, so the operator-visible text never says "killed" for a typo: the message string is "model not available" for both; the cause is carried in the audit `detail.cause` and the dedicated counter | same |
| Recheck refusal at provider open (6.4) | recoverable | first attempt (streaming, in `handle()`, or non-streaming): same 503 JSON envelope; streaming retry attempt after the head: `STREAM_*` error frame; cause `recheck` | same |
| Exception in the gate | bug | 503 `kill_switch_unavailable`, cause `gate_error` | ERROR with exception class |
| Audit emit fails | degraded | refusal still returned (the refusal does not depend on audit) | ERROR; audit loss is already visible in the GW14c completeness metrics |

Review finding 8: the 503 + `Retry-After` for `unresolvable_model`/`unknown_model` is DELIBERATE
(decision C25): the gateway has no 4xx posture code in the closed `STATUS_VOCAB` for a
client-attributable kill-path refusal, adding one would extend `rejected{status,reason}` series (5.10),
and answering a distinct 400/404 would let a client probe which model names exist in the catalog
versus are killed. The cost is that an SDK sees a retryable-looking status; `x-should-retry: false`
and the "model not available" message mitigate it. Revisit with GW28's client-facing model errors.
Every refusal (streaming or not, rendered as HTTP 503 with `Retry-After` and `x-should-retry: false`, 6.4a): zero provider calls, zero budget spent (P9), exactly one audit record in total over the request
(`audit.record.incident_refusal`, reason `REJECTED`, `detail.cause` from the closed
`INCIDENT_CAUSES`, status 503, `retry_after_ms`), one cause counter, one posture counter.

### 10.2 Mid-stream

| Condition | Result |
|---|---|
| Stale/unavailable snapshot at a chunk | `STREAM_SNAPSHOT_STALE` (existing precedence: dominates) |
| Global / org / org-model / model engaged | `STREAM_KILLED` |
| Key revoked (or ledger floor passed) | `STREAM_KEY_REVOKED` |
| Binding missing | `RefusingControlSource` -> `STREAM_SNAPSHOT_STALE` |
| Source raises | `InFlightControl.evaluate` is wrapped by the existing handler path; the factory's source catches, counts, and returns `available=False` (fail closed) |

### 10.3 Validation of external inputs

| Input | Required | Type / limits | On failure |
|---|---|---|---|
| KS record `key` | yes | one of the five scope shapes; slug `[-a-zA-Z0-9_]{1,64}`; model `MODEL_NAME` | `StoreDataUnavailable` (global fail closed, existing) |
| KS body `on` | yes | bool | `StoreDataUnavailable` (existing) |
| Isolation `fallback` | no | `MODEL_NAME` or absent/null | `malformed=True` (model-scoped refusal) |
| Isolation `origin` | no | `operator`/`auto`; MISSING means `operator` (then the 4.4 operator expiry rules apply unchanged); a present value outside the pair is invalid | malformed (invalid value only) |
| Isolation `expires_at` | rules in 4.4 | finite float | per 4.4 |
| Isolation `reason` | no | `[ -~]{0,200}` | truncated and flagged, never a failure |
| `AMF_MODEL_AUTO_TARGET` | no | one `MODEL_NAME` that is in the catalog | `ProfileError` at boot when set without a catalog or not in it; unset = `auto` refused (4.6) |
| `AMF_MODEL_CATALOG`, `AMF_MODEL_FALLBACKS` | no | JSON/`a=b` list, names `MODEL_NAME`, no self-fallback, no chain, max `risk_max_models()` entries | `ProfileError` at boot, loud |
| Requested model name (client) | yes (the parser may deliver absent/empty; the gate does not assume otherwise) | any str; `canonical_model` caps at 128, rejects control chars and non-NFKC-ASCII | `None` -> refuse `model_killed`/`unresolvable_model`; unknown to a configured catalog -> `unknown_model`; never a metric label; the canonical name is the only form used after phase 4b |

### 10.4 Invariant ownership

| Invariant | Owner layer and why |
|---|---|
| Absence never means off; unknown scope fails closed | `admit/killswitch.py` (it owns the attested snapshot) |
| One expiry decision | `admit/incident_state.py` (the read model every consumer shares) |
| Reroute never crosses compliance/sensitivity; no chain | `dispatch/incident_routing.py` (pure, testable, cannot reach a clock) |
| Refusal before quota; org from Principal | `edge/openai_chat.py` phase order, enforced by an ordering test (like GW36 S4 "gate short-circuit order") |
| No capacity literal | `runtime/resources.py` and the AST gate |
| Streams cut at next chunk | existing `edge/cancel.py` evaluator; GW29 supplies only the source |
| Notifier cannot affect enforcement | `edge/notifier_pump.py` (request path only appends) |

## 11. Handoffs and honest limits

- **GW28 handoff (D7):** `ConstraintSource`, `ModelCatalog`, `FallbackPolicy` are the seams. Until GW28,
  reroute requires an owner-declared `AMF_MODEL_CATALOG`; without it reroute is a declared refusal.
  GW28 should supply request-level compliance/sensitivity and per-org allowed models.
- **Control side (out of scope):** persisting auto-isolation events as signed `iso:<model>` records,
  notification delivery, Django models for isolation, console gauge. GW29 ships the writer method,
  the event, the seam and the data.
- **Workers are separate processes:** each worker scores and auto-isolates by its own traffic; fleet
  convergence rides the signed record. Low per-worker traffic delays a trip; this is declared, not
  hidden, and live L29-3 is an open gate.
- **Alias kill is a family kill:** killing a dated alias kills its canonical model (4.6, P13).
- **Isolation does not cut running streams (D2).** Kill does.
- **Streams admitted before a kill/revoke keep running only until the next chunk.** Per the runbook a
  kill, key revoke or stale snapshot cuts at the next chunk boundary (R2). The reverse also holds:
  a stream admitted BEFORE an isolation, and a non-streaming request already past its provider open
  when a kill lands, are not interrupted by isolation.
- **In-flight non-streaming requests (review finding 6):** a non-streaming request that has already
  opened the provider when a kill lands COMPLETES. The recheck of 6.4 covers only the window
  between phase 4b and the provider open (including retries). A post-response drop is NOT added:
  the response is already produced and billed, and dropping it adds a failure mode with no safety
  gain for content the org was authorised to receive at admission. This is a declared limit, owner
  can request the drop later (AC8 covers streams only).
- **Control-side OFF on expiry (review finding 7):** a signed `iso:<m>` record that expires stays
  `on:true` in the store until control writes OFF. Control SHOULD write that OFF (and the single
  RECOVER event/audit record, 5.3) at expiry; gateways do not depend on it (`active_isolation`
  applies expiry, and `incident_engaged_scopes` excludes expired signed isolations, 5.10).
- **Endpoint coverage (review r7 #2):** the kill switch covers `/v1/chat/completions`, `/v1/completions`,
  `/v1/responses`, `/v1/embeddings`, `/v1/moderations` and MCP. Declared exemptions, enforced by the 6.4b sweep
  test: `/v1/rag/query` and `/v1/vector/query` (placeholders that serve nothing) and `GET /v1/models*` (catalog
  listing, no tenant model call). Moderations and MCP honour global and org kills only (no routable model);
  a per-model kill or isolation does not apply to them.
- **`plan_changed` stays unwired (D11).**
- Pre-existing, untouched: `state_control/valkey.py:100` `check_tenant_scale` finding (R2-05).

## 12. Conservative fail-closed decisions (in addition to D1..D13)

| ID | Ambiguity | Decision |
|---|---|---|
| C1 | Where the incident check sits relative to admission | Phase 4b, before the CoDel slot and budget, so a refused request occupies nothing. |
| C2 | Mid-stream `age_s` vs the 20 ms `max_snapshot_age_s()` | Source reports `age_s=0.0` when fresh and `available=False` when stale; staleness judged only by `KillSwitchSnapshot.state()` (5 000 ms). Passing the real age would cut every stream. |
| C3 | Drift left the snapshot "OK" for up to 5 s | Drift now clears `_refreshed_at`, STALE immediately. |
| C4 | Org-wide default when `control` is unwired | `RefusingControlSource` (cut), not the old always-clear source; dev profile opts into `DevNoControlSource` by name. |
| C5 | Key-revoke memory overflow | Drop oldest, raise `floor_feed_seq`; streams admitted at or below the floor are cut as revoked. |
| C6 | Auto-isolation with no configured fallback | No isolation is created; an `AUTO_ISOLATE_SKIPPED` event and counter fire. Avoids a self-inflicted outage; kill switches remain the stop. Operator isolation with no fallback is a refusal (AC18). |
| C7 | Local auto-isolation table full | Evict the earliest-expiring entry and count it; never grow. |
| C8 | Operator-origin isolation with missing `expires_at` and no `no_expiry` flag | Malformed: that model's traffic is refused. Never silently permanent, never silently inactive. |
| C9 | GW28 absent | Reroute only through an owner-declared catalog; the fallback must be at least as compliant and sensitivity-capable as the isolated model. |
| C10 | Local vs signed isolation precedence | Active signed record wins; local entry is dropped once a signed active record for the model is seen; a signed `iso:<m>` OFF drops the local auto entry for that model (auto state only; 4.4). |
| C11 | Retry inside `run_with_no_splice` after an isolation lands | The attempt is refused (no reroute mid-request); zero provider calls to the isolated model. |
| C12 | Input blocks and gateway refusals as "guard outcomes" | Not counted; only output-guard hits attributable to the routed model are (the model did not cause an input block). |
| C13 | Rate-limit label/series growth | Dedicated closed-name counters; `REASON_VOCAB` not extended. |
| C14 | Model name variants (case, whitespace, alias, empty, unknown) | One `canonical_model` on writer, parser and gate; unresolvable or unknown (catalog configured) is refused; the canonical name is what is sent upstream (4.6). |
| C15 | Client-attributable provider failures and single-tenant abuse feeding auto-isolation | Closed outcome table; 4xx and gateway refusals never count; only catalogued models tracked; auto-isolation needs `distinct_error_orgs >= min(risk_min_distinct_orgs(), distinct_orgs_seen)` (5.5). |
| C16 | Revocation sequence basis | `record.feed_seq`, never the manifest value (5.7). |
| C17 | Recheck refusal and in-flight non-streaming | Recheck refusals have a stream code, a 503 envelope, a counter and one audit record; non-stream requests past provider open complete (6.4, 11). |
| C18 | `expires_at` skew | `wall() >= expires_at`, no grace, NTP for operator records (4.4). |
| C19 | A KS record with a known scope shape but an uncanonicalisable model segment | Per-record quarantine: it cannot match any admitted request, counts toward `on_count`, raises the `incident_quarantined_scopes` gauge, ERROR-logged once per window. Unknown scope SHAPES still raise (global fail closed). The writer validates first (4.6). |
| C20 | Auto-isolation to a fallback that is itself unusable | `decide_route` eligibility is run at trigger time; ineligible fallback means no isolation and `AUTO_ISOLATE_SKIPPED` / `fallback_ineligible` (5.6). |
| C21 | 429 handling in the score | Weight 0.5 in the formula only; sustained >= 70% 429 deliberately trips (5.5, 8). |
| C22 | Fleet scope-generation guard is unverified | Declared operator step; gateways publish `incident_scope_generation` so the operator can verify; projector cannot read it (9). |
| C24 | `auto`/absent model | Refused unless `AMF_MODEL_AUTO_TARGET` names a catalog model; never defaulted, never passed to kill matching as a non-model (4.6). |
| C25 | Status of client-attributable model refusals | Stays 503 `model_killed` + "model not available": no new 4xx in the closed status vocabulary, no catalog probing oracle (10.1). |
| C27 | AC27 for auto-isolation across N processes | Per detecting process at the gateway, one after dedup `(model, origin=auto)`; idempotent `iso:` writer; at-least-once notifier (5.3, 11). |
| C26 | Who emits signed-isolation transitions | Control only; gateways emit ISOLATE/RECOVER for process-local auto isolations only (5.3). |
| C28 | Endpoints that share the chat lifecycle (`/v1/completions`, `/v1/responses`) | Covered by construction through `run_lifecycle`; the shared helpers take a REQUIRED `outcome` keyword, a missing binding is a refusal never a silent no-kill, Responses rechecks before its SSE head (6.3a, review r7 #1). |
| C29 | Endpoints with their own admission path (embeddings, moderations, MCP) | Wired, not declared a gap: embeddings gets the full gate and never reroutes (`reroute_unsupported`), moderations and MCP get global+org `decide_org`; fail-closed defaults; route-table sweep test (6.4b, review r7 #2). |
| C30 | Isolation record with no `origin` field | Missing `origin` is `operator`, then the operator expiry rules apply unchanged; only a present invalid value is malformed (4.4, review r7 #5). |
| C23 | Cause string for unknown model | Posture code stays `model_killed`; client message says "model not available"; cause lives in audit and counter (10.1). |

## 13. Correctness properties and tests

House idiom: seeded `random.Random`, each property a loop of 10 000 iterations (stated once; the
requirements raise the card's floor above the generic 100), seed in every assertion message, no
`hypothesis`, `asyncio.run` where async. Each file starts with the in-source tag
`# Feature: gw29-killswitch-isolation, Property N: <text>` and a `Validates:` line. Files are
prefixed `test_lgw29_` under `tests/admit/`, `tests/dispatch/`, `tests/edge/`, `tests/runtime/`,
`tests/state_control/`.

| P | Property | File (area) |
|---|---|---|
| P1 | For any random engaged set of global/org/org-model/model scopes and any request, an engaged applicable scope is never admitted | `tests/admit/test_lgw29_gate_property.py` |
| P2 | Other orgs and other models are unaffected by an engage of org A / model M | same |
| P3 | STALE, never loaded, or drifted (random manifest mismatch) is never admitted | `tests/admit/test_lgw29_stale_property.py` |
| P4 | `decide_route` never names a killed or isolated model; equal inputs give equal outputs (determinism) | `tests/dispatch/test_lgw29_routing_property.py` |
| P5 | A reroute never lowers compliance tags or sensitivity ceiling; unknown constraints always refuse | same |
| P6 | Expired isolation is never active on the gate or in `snapshot_models`; the two always agree (random clock jumps, missing/malformed `expires_at`) | `tests/admit/test_lgw29_expiry_property.py` |
| P7 | Score in `[0,1]`, `0` below min samples, monotone in weighted errors at fixed total, probation uses only post-recovery samples; cases pin 50% 429 (score 0.25, no trip) and 100% 429 (score 0.5, trips) | `tests/admit/test_lgw29_risk_property.py` |
| P8 | Two assertions for any engage time and chunk cadence under an injected clock: (a) `cut_time - applied_time <= max_cut_latency_s()` where `applied_time` is the injected-clock instant `KillSwitchSnapshot` applied the engage record (so a cut delayed after RAM visibility FAILS, FRESH_MS is not in this bound); (b) end to end `cut_time - signed_write_time <= FRESH_MS + max_cut_latency_s()` | `tests/edge/test_lgw29_cut_bound_property.py` |
| P9 | A refused request charges zero budget, takes zero slot, makes zero provider calls (real `QuotaGate` + fake provider ledger) | `tests/edge/test_lgw29_refusal_charges_nothing_property.py` |
| P10 | Publisher rendered series count equals declared, invariant to tenant count and to the number of models beyond K | `tests/edge/test_lgw29_series_count_property.py` |
| P11 | Revocation ledger: after any revoke/overflow sequence, `revoked_since` is True for every key revoked after the stream's admit, using `record.feed_seq` (never the manifest value), including delete/re-add/delete sequences (never a false negative); an empty ledger (`floor_feed_seq == -1`) never reports revoked, even for `feed_seq` 0 | `tests/admit/test_lgw29_revocation_property.py` |
| P12 | Auto-isolation tightens only: never removes a kill, never overrides or shortens an operator isolation (a signed OFF drops only local auto state); never isolates when the fallback is missing, killed, isolated, or constraint-ineligible (emits `AUTO_ISOLATE_SKIPPED` with the reason, creates no entry) | `tests/admit/test_lgw29_autoisolate_property.py` |
| P13 | For any case/whitespace/NFKC/alias mutation of an engaged model (kill, org-model or isolation), the request is refused or rerouted, never sent to the killed model; unresolvable/unknown names are refused; the upstream body carries the canonical name; a kill of a dated alias refuses the whole canonical family | `tests/admit/test_lgw29_model_identity_property.py` |
| P14 | Outcome mapping: client-attributable statuses and gateway refusals never change a score; one outcome per attempt; with >= `risk_min_distinct_orgs()` active orgs, errors from fewer than the floor never trip; with 1 active org it trips; with 2 active and errors from 1 it does not; with 3+ active and errors from 3 it trips (all via `distinct_error_orgs >= min(risk_min_distinct_orgs(), distinct_orgs_seen)`); untracked names never occupy slots | `tests/admit/test_lgw29_outcome_property.py` |
| P15 | `GateVerdict` exactly-one invariant holds for every constructor path and every gate input; any refusal (phase 4b or recheck, streaming or not) yields exactly one audit record over the whole `handle()` call, one counter and one observer refusal outcome | `tests/admit/test_lgw29_verdict_property.py` |

Review r7 additions (each file under `tests/edge/` unless noted, prefix `test_lgw29_`): `endpoint_matrix` (3 endpoints x
stream/non-stream x refusal/recheck/cut, plus the clean `/v1/responses` regression, 6.3a), `callers_pass_outcome` (AST
tripwire, 6.3a), `upstream_model` (canonical name in the provider-open body and `GatewayRequest`, 6.2), `misc_surfaces`
(embeddings, moderations, MCP, route-table sweep, 6.4b), and in `tests/admit/` the isolation parser matrix gains the
rows `origin` missing + finite `expires_at` (active until expiry, then inactive), `origin` missing + `expires_at: null`
+ `no_expiry: true` (permanent), `origin` missing + neither (malformed), `origin: "bogus"` (malformed).

Example/unit tests (same prefix): scope grammar and body parser (valid/invalid matrix), `evaluate`
single-lock behaviour, drift latch (C3), explicit OFF re-admit and engage-then-disengage within one
refresh (AC6), org from Principal not header (AC7), `age_s` pin (C2), `RefusingControlSource` and
production-wiring-is-not-`DevNoControlSource` (AC12), migration of every `build_app(` caller (5.8), recheck refusal rendering streaming and non-streaming (6.4), operator OFF clearing local auto state (4.4), org-model refuses only that org (AC13),
projector matrix flag ON/OFF, per-key still unsupported, explicit OFF and `on_count` (AC14,
Postgres 16 via `AMF_PG_DSN` when set, declared skipped with reason otherwise), old-applier/new-record
round trip (AC15), notifier raising/blocking does not change the outcome (AC28), no content/key in
events, audit, logs, metrics (AC30), phase-order test (4b before admission and budget), attempt
re-check (C11), `MappingProxyType`/frozen/slots assertions for new value types.

Local equivalents of the live tests, with injected clock and fake provider: L29-1
(`tests/edge/test_lgw29_l29_1_kill_under_load.py`, includes G-17: 2 000-token stream, engage at
+2 s, cut asserted), L29-2 (isolate with fallback, zero calls to M), L29-3 (50% injected provider 5xx or timeout, NOT 429 (429 weighs 0.5: a 50% 429 rate scores 0.25 and does not trip, 100% 429 scores 0.5 and does, and the L29-3 run injects 5xx/timeouts so it is unaffected), spread across >= `min(risk_min_distinct_orgs(), orgs active in the window)` orgs (a one-org run is valid and must trip; a two-org run with errors from one org must not), auto-isolation inside `risk_window_s()`, notification recorded, traffic on the fallback, recovery,
no flapping), L29-4 (expiry, request path and read model agree within one refresh).
A micro-benchmark (`tests/edge/bench_lgw29_hot_path.py`, run outside the suite, output in evidence)
records added per-request and per-chunk cost (N1, N2).

Negative controls (AC32, recorded in evidence, each reverted after the run): remove the phase 4b
call; restore `DevNoControlSource` as the production default; make the gate treat STALE as OK; make
expiry compare against creation only. Each must make a named acceptance test fail.

## 14. Offline gate obligations

Run in the same container-run style as sibling cards, from `gateway_v2/`:

1. Record the baseline: full offline `pytest` count (passed/skipped/xfailed) on the branch before the
   first code commit. The final run must have 0 failed and no new skips versus baseline other than the
   declared Postgres-only tests.
2. Full offline suite after each layer commit (domain, runtime, audit, dispatch, admit, edge,
   state_control), lgw29 subset after each commit.
3. `mypy --strict` over `gateway_v2` and `state_control` (touched files clean; no new `type: ignore`).
4. `ruff check` clean.
5. `import-linter`: both contracts kept, no new ignore, no `egress -> detect` edge, `state_control`
   imports only `gateway_v2.domain`.
6. The five AST gates (`capacity_literals`, `no_module_mutable`, `frozen_dataclasses`,
   `http_outside_edge_resolve`, `tenant_scale`) exit 0 over `gateway_v2` and `state_control`, except the
   documented pre-existing `state_control/valkey.py:100` finding. The `capacity_literals` gate is
   the proof that no threshold, window, count or duration literal landed outside
   `runtime/resources.py`; the frozen/slots gate is the proof for value types; the
   `no_module_mutable` gate is the proof for module state (all new tuples are tuples, mappings are
   `MappingProxyType`, regexes are compiled constants).
7. Module-size gate: new modules stay under the shared size limit (each is split by responsibility
   above for that reason).
8. Evidence under `docs/plans/evidence/<date>-gw29/` (baseline, final run, gates, benchmark,
   negative controls), the plan under `docs/plans/<date>-gw29-*.md`, an `AGENTS.md` GW29 changelog
   entry and the progress-doc card-table update, stating closed-local vs open.

9. Verify-first grep task (first edge task, output kept in the evidence): `grep -rn "_stream_response\|collect_and_render\|
   run_lifecycle\|collect_and_enforce\|_collect_output_channels\|with_openai_body\|_proceed(" gateway_v2 tests` and every hit is
   classified (updated, or proven not to need `outcome`/`model`), so no caller of a changed helper is missed.

**Declared OPEN (cannot close locally):** live L29-1..L29-4 on the real stack; real notification
delivery; fleet-wide convergence timing of a signed auto-isolation; console gauge and display
agreement in a browser (GW35).

## 15. Review responses

### 15.7 Revision 7 (responses to `design-review.md` on revision 6: 1 HIGH, 2 MEDIUM, 2 LOW)
| # | Sev | Response |
|---|---|---|
| 1 | HIGH | FIXED. New 6.3a lists every caller of the shared helpers (read in `routes.py`, `openai_chat.py`, `openai_nonstream.py`, `openai_responses.py`). 6.3 now gives a code-verified signature table: `outcome` is a REQUIRED keyword on `_stream_response`/`collect_and_render`, `org_id`/`routed_model` required on `collect_and_enforce`/`_collect_output_channels`/`_next_attempt`; `openai_responses.py` passes `outcome=outcome` at its two call sites. Responses: refusals go through the incident-aware `_emit_error_code(retry_after_s=...)` (503 + `Retry-After` + `x-should-retry: false`, one audit record via the `note_incident_cause` that `run_lifecycle` now calls); the first-attempt recheck is the shared `ChatRoute.first_attempt_refusal`, called before `start_sse`; a mid-stream cut maps to `response.failed`. Per-endpoint matrix test, a clean-Responses regression test, an AST caller tripwire and a grep task were added (6.3a, 13, 14, C28). |
| 2 | MEDIUM | FIXED with option (a). Embeddings gets the full gate before `_admit` plus a recheck above `mark_dispatched` and never reroutes; moderations and MCP get global+org `decide_org`; fail-closed defaults on `MiscRoute`; route-table sweep test; declared exemptions limited to placeholders and `GET /v1/models*` (6.4b, 5.4, 11, C29). |
| 3 | MEDIUM | FIXED. `_proceed(plan, req, decision, request_id, *, binding, verdict)`; the binding is built in `run_lifecycle` where `identity` and `key_hash` are in scope, and `note_reroute` is called there (6.1). The "model slot" wording is withdrawn: `with_openai_body(..., *, model: str)` fills `body["model"]`, `_proceed` passes `verdict.routed_model` to both `GatewayRequest` and `with_openai_body`; the request-id fingerprint intentionally keeps the client string and why that is safe is recorded; `test_lgw29_upstream_model.py` added (6.2). |
| 4 | LOW | FIXED. Status line is REVISION 7; section 15 headings renumbered 15.7 down to 15.1, and the one cross-reference (15.0 to 15.4) updated. |
| 5 | LOW | FIXED. One rule: missing `origin` is `operator`, then the 4.4 operator expiry rules apply; table row in 10.3, C30 and the parser matrix rows added (4.4, 10.3, 13). |
Canonical model identity before matching, fail-closed defaults, layering and the no-capacity-literal rule are unchanged. No new file with capacity literals is added (`runtime/resources.py` stays the only home; `runtime/resources_org.py` from GW27 D-13 is not used or extended here).

### 15.6 Revision 6 (responses to `design-review.md` on revision 5: 0 HIGH, 3 MEDIUM, 1 LOW, 1 NIT)
| # | Sev | Response |
|---|---|---|
| 1 | MEDIUM | ADDRESSED. `render_http(retry_after_s)`, `_emit_error_code`, `_render_refusal` edits and the header test are listed in 6.4a; `handle()` passes `outcome.retry_after_s`. |
| 2 | MEDIUM | ADDRESSED. Incident codes render as HTTP 503 even for streaming, via a branch before the streaming branch; recorded as a deliberate difference with a test (6.4a). |
| 3 | MEDIUM | ADDRESSED. `holds.note_incident_cause`, suppression of the generic admission record, `refused_code` local for the recheck, and total-record-count assertions in P15 and the refund tests (6.4a, 5.9, 13). |
| 4 | LOW | ADDRESSED. The cut record replaces the generic terminal record; count tested (6.4a, 5.9). |
| 5 | NIT | ADDRESSED. `self.control` typed as the `InFlightControlSource` protocol (6.3). |
Canonical model identity before matching, fail-closed defaults, layering and the no-capacity-literal rule are unchanged.
### 15.5 Revision 5 (responses to `design-review.md` on revision 4: 1 HIGH, 1 MEDIUM, 3 LOW, 1 NIT)
| # | Sev | Response |
|---|---|---|
| 1 | HIGH | ADDRESSED. First-attempt streaming recheck moved to `ChatRoute.handle` before `_stream_response` (still before `mark_dispatched`), so the 503 JSON is producible; retry attempts keep the error-frame path (6.4, 10.1, test a/c). |
| 2 | MEDIUM | ADDRESSED. "Already carries" removed; `cause` added to `LifecycleOutcome`; `_refused` and the signature changes listed; `org_id` from `outcome.binding` (6.1, 6.3). |
| 3 | LOW | ADDRESSED. `_refreshed_at` cleared on every `StoreDataUnavailable` from `_absorb_locked` (4.3). |
| 4 | LOW | ADDRESSED. `floor_feed_seq` starts at -1; empty-ledger case added to P11 (5.7). |
| 5 | LOW | ADDRESSED. Alias-kill-is-family-kill added to 4.6 and 11 and P13. |
| 6 | NIT | ADDRESSED. Line numbers replaced by a re-grep instruction (6.3). |
Canonical model identity before matching, fail-closed defaults, layering and the no-capacity-literal rule are unchanged.
### 15.4 Revision 4 (responses to `design-review.md` on revision 3: 0 HIGH, 2 MEDIUM, 2 LOW, 1 NIT)
| # | Sev | Response |
|---|---|---|
| 1 | MEDIUM | ADDRESSED. One expression `distinct_error_orgs >= min(risk_min_distinct_orgs(), distinct_orgs_seen)` used verbatim in 5.5, 5.6, C15, P14; P14 reworded to the 1/2/3+ org cases. |
| 2 | MEDIUM | ADDRESSED with a recorded deviation (C27). AC27 holds per detecting process at the gateway and once after dedup: `IncidentEvent.dedup_key = (model, origin=auto)` over `auto_isolation_duration_s()`, at-least-once notifier, idempotent `StateWriter.isolation` for an active auto `iso:<model>`, N=8 tracker test (5.3, 11). Fleet-wide RECOVER likewise dedups on the same key. |
| 3 | LOW | ADDRESSED. `incident_recheck` typed `Callable[..., GateVerdict]` and called with keywords; `fail_closed_recheck(*, org_id, model)` defined in 5.4. |
| 4 | LOW | ADDRESSED. All five posture codes listed in 5.10; `domain/posture.py` `__all__` edit stated. |
| 5 | NIT | ADDRESSED. Blank line added before the signed-record paragraph in 5.3. |
Canonical model identity before matching, fail-closed defaults, layering and the no-capacity-literal rule are unchanged.

### 15.3 Revision 3 (responses to `design-review.md` on revision 2: 0 HIGH, 5 MEDIUM, 4 NIT)
The earlier HIGH (model identity canonicalised before matching, no raw-string kill bypass) is
unchanged and honoured: finding 5's fix keeps `canonical_model` as step 0 and only defines what the
sentinel does after it.
| # | Sev | Response |
|---|---|---|
| 1 | MEDIUM | ADDRESSED. Recheck is called BEFORE `holds.mark_dispatched()` (`routes.py:689`, `openai_nonstream.py:198`); refusal leaves holds undispatched, so `RequestHolds.release` calls `quota.refund(cost, request_id, owner)` -> `QuotaGate.refund` -> `QuotaComponent.refund` (`admit/quota.py:406`); retry-attempt case declared; tests (a)-(d) incl. AST order test and negative control (6.4). |
| 2 | MEDIUM | ADDRESSED. Trip needs `distinct_error_orgs >= min(risk_min_distinct_orgs(), distinct_orgs_seen)` with `orgs_seen` counting OK samples; P14 cases for 1, 2, 3+ active orgs; L29-3 procedure note (5.5, 13). |
| 3 | MEDIUM | ADDRESSED. Rule: gateways emit ISOLATE/RECOVER for process-local auto isolations only; signed transitions are control's single event; `sweep()` returns `SweepResult(local_expired, signed_expired)`; N-snapshot test (5.3, 5.6, C26). |
| 4 | MEDIUM | ADDRESSED. `RequestHolds.note_reroute(RerouteNote)` + `reroute` property; audit `detail.routing.*`, stream trace, non-stream `x-amf-routing-reason` header; file list and streaming/non-streaming example tests (6.2). |
| 5 | MEDIUM | ADDRESSED. `auto` canonicalises to `auto` (contradictory sentence removed); absent/empty refused; `auto` refused with or without a catalog unless `AMF_MODEL_AUTO_TARGET` names a catalog model, substituted before step a (4.1, 4.6, 10.1, C24). |
| 6 | NIT | ADDRESSED. Single name `active_isolation(model)` in 4.1; lock order S and I never nested, with concurrency and AST tests (5.3). |
| 7 | NIT | ADDRESSED. `incident_engaged_scopes` = `view.count - expired_signed_count()`; control OFF-on-expiry recorded as a handoff (5.10, 11). |
| 8 | NIT | ADDRESSED. 503 for client-attributable model refusals documented as deliberate (no new status, no probing oracle) (10.1, C25). |
| 9 | NIT | ADDRESSED. PromQL `min(incident_scope_generation) >= 2` plus `count(...) == count(up{...})` completeness check (9). |
Unverified assumptions from the review: lifespan hook hosting the pump/ticker remains to be read at
implementation (first edge commit) and is listed as a verify-first task; `capacity_literals` on
domain constants is confirmed in the domain commit; `with_openai_body` `model_override` is read by the
implementer with a `None` slot-mismatch fail-closed path (6.2).
### 15.2 Revision 2 (responses to the earlier `design-review.md`, 0 HIGH, 7 MEDIUM, 4 NIT)

| # | Sev | Response |
|---|---|---|
| 1 | MEDIUM | ADDRESSED. Trigger-time `decide_route` eligibility; `AUTO_ISOLATE_SKIPPED` / `fallback_ineligible`; operator `iso:` with ineligible fallback yields declared `REROUTE_REFUSED`; added to P12 (5.6, C20). |
| 2 | MEDIUM | ADDRESSED. Chose the recommended reading: 429 weight 0.5 in the formula only, sustained >= 70% trips; table, section 8 and the L29-3 note corrected; P7 cases for 50% and 100% 429 (5.5, 8, 13, C21). |
| 3 | MEDIUM | ADDRESSED. `aliases` constructor arg and `state_runtime.py:183` wiring; writer canonicalises without aliases, gateway applies aliases at read; per-record quarantine instead of a fleet-wide 503 (decision C19); catalog digest logged and checked in readiness (4.6). |
| 4 | MEDIUM | ADDRESSED. Event to phase to reason to emitter table (5.11); `audit_emit.py` in the edit list; `incident_engaged_scopes` and `incident_quarantined_scopes` gauges; SERIES_COUNT recomputed to 27 (5.10). |
| 5 | MEDIUM | ADDRESSED. `incident_recheck` on `RouteDeps` and the non-stream deps with a fail-closed default; exact sites `routes.py:635/691` and `openai_nonstream.py:170/199`; first-attempt refusal is the 503 envelope before the head, later is the error frame (6.3, 6.4). |
| 6 | MEDIUM | ADDRESSED. P8 split into post-visibility `<= max_cut_latency_s()` and end-to-end `<= FRESH_MS + max_cut_latency_s()` (4.2, 13). |
| 7 | MEDIUM | ADDRESSED. `IncidentTicker` in the lifespan calling `sweep()`; `IncidentState.sweep` returns newly expired models once and purges `_first_seen`; one `active_isolation(model)` signature (5.3, 5.6). |
| 8 | NIT | ADDRESSED. Message "model not available", cause in audit and counter (10.1, C23). |
| 9 | NIT | ADDRESSED. Limit stated; gateways publish `incident_scope_generation` (9, C22). |
| 10 | NIT | ADDRESSED. `MODEL_NAME_MAX`, `SLUG_MAX`, `REASON_MAX` named constants with a guard test (5.1). |
| 11 | NIT | ADDRESSED. 10 000 stated once (13). |

### 15.1 Revision 1 (retained)

Revision 1 responses to the earlier `design-review.md` (verdict CHANGES_REQUESTED):

| # | Sev | Response |
|---|---|---|
| 1 | HIGH | ADDRESSED. `canonical_model` in `domain/incident.py` used by writer, `parse_scope` and gate; unresolvable/unknown refused; canonical name sent upstream (4.6, 6.2, C14); mutation property P13. |
| 2 | MEDIUM | ADDRESSED. Explicit outcome table, catalogued-only tracking, `risk_min_distinct_orgs()` knob, one outcome per attempt with fixed guard-hit arithmetic, L29-3 injects 5xx (5.5, 8, C15, P14). |
| 3 | MEDIUM | ADDRESSED. Single frozen slotted `GateVerdict` with exactly-one invariant; `IncidentDecider` and `fail_closed_incident` signatures given (5.4, P15). |
| 4 | MEDIUM | ADDRESSED. `STREAM_MODEL_ISOLATED`, `incident_refused_recheck_total` (SERIES_COUNT 27 in revision 2), cause `recheck`, same 503 envelope non-stream, one audit record (6.4, 5.10, C17). |
| 5 | MEDIUM | ADDRESSED. Ledger stores `record.feed_seq`; manifest parameter dropped; re-created key defined; added to P11 (5.7, C16). |
| 6 | MEDIUM | ADDRESSED. Call-site migration list and `DevNoControlSource` fixture (5.8); in-flight non-streaming behaviour declared (11). |
| 7 | NIT | ADDRESSED. Signed OFF of `iso:<m>` drops local auto state, tightening-only preserved (4.4, C10). |
| 8 | NIT | ADDRESSED. `wall() >= expires_at`, no grace, NTP (4.4, C18). |
| 9 | NIT | ADDRESSED. L29-3 and AC22 mean 50% 5xx or timeout; 429 explicitly excluded (13). |

Requirements text for AC22 is not edited by this step; the design pins its interpretation as above.
