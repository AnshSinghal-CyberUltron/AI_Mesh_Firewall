# Requirements: GW29 Incident response (kill switch, isolation, reroute, auto-isolation)

**Status:** DRAFT for design. Authored unattended; every ambiguity is resolved by a recorded
conservative fail-closed decision (section 9), not left open.

**Branch / tree:** `gw29-killswitch-isolation` (worktree `.worktrees/gw29`, off `suraj-revamp`).
**Source root:** `gateway_v2/gateway_v2/`. **Tests:** `gateway_v2/tests/<area>/`.
**Runbook card:** GW29. Phase Admission. Depends on GW05b, GW06, GW28. Required by GW21, GW23.
Finding V-04 (Blocking): "Kill-switch state is never consulted on the request path or mid-stream."

## 1. Summary

The operator's emergency controls do not stop traffic on gateway v2. The signed kill-switch state
is already written, replicated and held in RAM, but nothing reads it where it matters:

- `admit/killswitch.py` has a complete `KillSwitchSnapshot` (global / `org:` / `model:` scopes,
  attested `on_count`, STALE detection). `edge/state_runtime.py:183` builds one and
  `edge/state_runtime.py:205,389` keep it current from the `StateKind.KS` feed. No request-path code
  calls `state()`, `org_killed()` or `model_killed()`.
- The mid-stream seam is `edge/cancel.py` (`InFlightSnapshot`, `InFlightControlSource`,
  `InFlightControl`). The only source ever supplied is `_NoControlSource`
  (`edge/app.py:769-790`), which returns `kill_switch_engaged=False`, `key_revoked=False`,
  `plan_changed=False`, `age_s=0.0`, `available=True` always. `edge/serve.py:204` and
  `edge/routes.py:264,489` use it as the default.
- `edge/production_profile.py:8,492` logs "kill switch -> snapshot kept current, NOT enforced on the
  request path (GW29)".
- `state_control/projector.py` (GW36 S2) writes only global and `org:<slug>` ON/OFF records. An
  engaged per-org model switch, or one with a credential prefix, has no v2 scope and is logged as
  "NOT enforced by gateway v2 (GW29)" (`state_control/projector.py` around lines 455-480). Mapping
  an org's model switch onto `model:<name>` would stop that model for every tenant.
- There is no isolation, reroute, risk score, auto-isolation or notification code. `dispatch/routing.py`
  `DispatchRouter.select(plan)` is a pure function of plan identity; `ExecutionPlan`
  (`domain/plan.py:89`) carries no model, fallback, compliance or sensitivity field.

GW29 builds the enforcement: (a) consult the kill switch at admission and mid-stream; (b) org-wide
and per-model kill switches that really refuse traffic; (c) isolation and reroute as plan state;
(d) a per-model risk score and auto-isolation with reconciled expiry; (e) notification on isolate,
reroute and recovery. It also closes the known item "streams admitted before a revoke or kill keep
running" per the owner's section 0.7-1 working default (cut at the next chunk).

## 2. Authoritative card text (quoted from AI_MESH_MASTER_RUNBOOK_v3.1_CLEAN.docx)

> **GW29 - Incident response: kill-switch, isolation, reroute and auto-isolation.** Phase Admission.
> Depends on GW05b, GW06, GW28. Required by GW21, GW23.
>
> **Objective.** Make the operator's emergency controls stop traffic: global, org and model
> kill-switch; one-click model isolation; reroute to a configured fallback; a live risk score; auto-isolation
> on provider error spikes; and notification.
>
> **Implementation work.**
> 1. Construct KillSwitchSnapshot from the state feed and consult it at admission and mid-stream
>    (replacing the hard-coded kill_switch_engaged=False). Settle section 0.7-1 first.
> 2. Isolation and reroute as plan state. Reroute must not cross compliance or sensitivity
>    constraints. A fallback that is itself killed or isolated is a declared refusal.
> 3. Risk score per model from provider errors and guard outcomes, exposed for the console's 5-second
>    gauge. Auto-isolate above a threshold, with expiry reconciled. On production, a v1 isolation
>    lease that expired on 23 July 2026 was still displayed as active in August, while traffic to
>    that model was served.
> 4. Notification on isolate, reroute and recovery.
>
> | Test | Live procedure | PASS |
> |---|---|---|
> | L29-1 | Engage a model kill-switch under load. | New requests refused within the freshness bound; in-flight streams follow the section 0.7-1 decision. |
> | L29-2 | Isolate a model with a fallback. | Traffic reroutes; zero requests reach the isolated model; routing reason shows the reroute. |
> | L29-3 | Inject a 50% provider error rate. | Model auto-isolates within the declared window; notification sent; traffic recovers on the fallback. |
> | L29-4 | Isolation expires. | Every display surface and the request path agree within one refresh. |

Related runbook text carried into scope:

- Register row: "Kill switch | State only (admit/killswitch.py) | No | KillSwitchSnapshot is never
  constructed outside tests; in-flight check hard-coded kill_switch_engaged=False (edge/app.py:770-777)."
  (the snapshot is now constructed by GW36 S3; the in-flight and admission consumption is still absent).
- Section 0.7-1: "In-flight kill-switch and revocation semantics (G-17, v2.1 C24). RC2 lets a stream
  that started before a kill switch finish. Decide whether it continues, or is cut at the next chunk
  with the terminal error frame."
- Gate G-17 (GW12): "In-flight kill-switch semantics. 2,000-token stream; engage the org's kill
  switch at +2 s. Matches the owner's decision (section 0.7-1)."
- Progress doc, GW36 working defaults (2 Nov): "in-flight kill switch cuts at the next chunk;
  RV_STATE_FRESH_MS 5000". Progress doc GW36 S2/S3 "Recorded, not fixed": org-plus-model switches
  "counted and logged as not enforced until GW29"; "Streams admitted before a revoke keep running (GW29)".
- Progress doc, card table: "GW29 ... Not started. kill_switch_engaged is still hard-coded false."

## 3. Existing contract GW29 must honour (signed feed)

GW29 consumes; it does not redefine. Any change to the following must be additive and
backwards-compatible, and must be justified in the design.

| Item | Contract (as found in the tree) | Source |
|---|---|---|
| Record kind | `StateKind.KS`, signed `SignedRecord`, `deleted` flag, `feed_seq` | `domain/state.py`, `runtime/state_sig.py` |
| Scope grammar | key is `global`, `org:<id>`, or `model:<name>`; `model:<name>` is GLOBAL across tenants | `admit/killswitch.py:41-43,222-241` |
| Body | JSON object with boolean `on`. Non-bool or missing `on`, invalid JSON, non-object: `StoreDataUnavailable` (fail closed) | `scope_is_on`, `admit/killswitch.py:61-83` |
| Explicit OFF | C36: an explicit `{"on": false}` record exists for every scope; absence never means off | `state_control/projector.py:268`, `state_control/writer.py:239-241` |
| Manifest | `on_count` (attested engaged scopes) checked after every COMPLETE round; mismatch makes the snapshot unavailable; records above `attested_feed_seq` are not applied | `admit/killswitch.py:200-215`, `state_control/pg.py` |
| Unknown scope | raises `StoreDataUnavailable` ("could be an engaged switch we would skip") | `_set_scope` |
| Freshness | `FRESH_MS = 5_000`; two independent staleness paths (local refresh age, and the re-hydrator `FreshnessGate` stamp) | `domain/locks.py:8`, `KillSwitchSnapshot.state()` |
| States | `KillSwitchState.OK / ENGAGED / STALE`, evaluated from RAM only, no store call on the request path | `admit/killswitch.py:46-52` |
| Projector (writer) | `state_control/projector.py` writes `global` and `org:<slug>` ON for a disable action with model `_ORG_WIDE`, no credential prefix; everything else is `unsupported` and noted | `state_control/projector.py` `_Desired` build, `_switches_on/_off` |
| Phase ordering | kill switches ON first, OFF last, so traffic stops before anything else moves | `state_control/projector.py` module docstring |
| Refusal posture | `KILL_SWITCH_UNAVAILABLE` -> 503 `service_unavailable`; `STREAM_KILLED`, `STREAM_KEY_REVOKED`, `STREAM_SNAPSHOT_STALE` already exist | `domain/posture.py`, `edge/errors.py:269` |
| In-flight seam | `InFlightSnapshot(available, age_s, kill_switch_engaged, key_revoked, plan_changed)`; evaluator order: stale/unavailable, max duration, kill, revoked, plan change; cut lands by the next chunk boundary within `max_cut_latency_s()` | `edge/cancel.py:329-480` |

## 4. System and user stories

- **S1 Operator (global stop).** As an operator I engage the global kill switch so no tenant request
  is admitted and no running stream continues, within the freshness bound.
- **S2 Operator (org stop).** As an operator I engage an org kill switch so only that org's new
  requests are refused and its running streams are cut, with other orgs unaffected.
- **S3 Operator (model stop).** As an operator I engage a model kill switch so no request resolves
  to that model and any stream already on it is cut.
- **S4 Operator (one-click isolation).** As an operator I isolate a model with a configured
  fallback so traffic moves to the fallback and zero requests reach the isolated model, and the
  routing reason says why.
- **S5 Security owner (no cross-constraint reroute).** As a security owner I need a reroute never to
  land a request on a model that violates the org's compliance or sensitivity constraints, and a
  fallback that is itself killed or isolated to produce a declared refusal, not a silent hop.
- **S6 SRE (auto-isolation).** As an SRE I need a model with a provider error spike to isolate
  itself within a declared window, notify me, send traffic to the fallback, and recover without
  manual cleanup.
- **S7 Console user (honest gauge).** As a console user I need a per-model risk score that refreshes
  at 5 s cadence and an isolation state that matches what the request path actually does, including
  after expiry (the July/August v1 defect).
- **S8 Gateway (fail-closed).** As the gateway, when I cannot establish kill-switch state (stale,
  unavailable, drifted, unknown scope), I refuse rather than serve.
- **S9 Security owner (revoked key).** As a security owner, a key revoked while a stream is running
  cuts that stream at the next chunk (same seam as the kill switch).

## 5. Functional requirements

### 5.1 Kill-switch enforcement at admission (R1)

1. The admission path SHALL consult the per-process `KillSwitchSnapshot` after identity is
   resolved and before quota is charged and before any provider dispatch. RAM only; no store call
   on the request path. "The admission path" means every authenticated endpoint that can serve a
   tenant: chat completions, completions, responses, embeddings, moderations and MCP (design 6.3a,
   6.4b); only placeholder routes and the model catalog listing are exempt.
2. It SHALL evaluate in this order: snapshot STALE -> refuse (fail closed); global engaged -> refuse;
   org engaged for the resolved org -> refuse; model engaged for the routed model -> refuse.
3. Every refusal SHALL be a declared posture code rendered by `edge/errors.py`, with
   `x-should-retry: false` semantics and a jittered `Retry-After` of at least `MIN_RETRY_AFTER_S`,
   consistent with the shed/budget refusals (`admit/grant.py`). A killed org's request SHALL NOT
   charge budget or consume a quota lease token.
4. Refusals SHALL emit an audit record with a reason (C40), be counted on a closed-vocabulary
   rejected counter (real zeros, no new unbounded label), and never log request content.
5. Org identity SHALL come from the resolved `Principal`, never from a client header or body field.

### 5.2 Mid-stream enforcement (R2)

1. GW29 SHALL provide the real `InFlightControlSource`, replacing `_NoControlSource` as the
   default in `edge/app.py` and `edge/serve.py`, built per stream (bound to that stream's org id,
   routed model and key hash) because the shipped protocol `snapshot()` takes no arguments and the
   handler holds one shared instance (`edge/routes.py:264`).
2. `kill_switch_engaged` SHALL be true when global, the stream's org, or the stream's routed model is
   engaged. `available` and `age_s` SHALL be derived from the kill-switch snapshot state and
   refresh age, so STALE or never-loaded maps to `available=False` (-> `STREAM_SNAPSHOT_STALE` cut).
3. `key_revoked` SHALL be true when the stream's admitting key hash is no longer live in the
   identity state (closing "Streams admitted before a revoke keep running", progress doc S3).
4. Per owner decision 0.7-1 (working default, section 9 D1) the stream is cut at the next chunk
   boundary with the declared terminal error frame: `STREAM_KILLED`, `STREAM_KEY_REVOKED`, or
   `STREAM_SNAPSHOT_STALE`. No bytes from the provider reach the client after the cut (R10.7 in
   the GW12 contract) and the provider is aborted.
5. The cut SHALL land within the freshness bound plus `max_cut_latency_s()` of the signed
   engage becoming visible in RAM.

### 5.3 Org-wide and per-model scopes (R3)

1. Global and `org:<id>` switches SHALL be enforced exactly as in R1/R2.
2. A per-org model switch ("block model M for org O") SHALL be enforceable without turning into a
   global model stop. Decision D4 requires an additive org-plus-model scope in the feed; the
   `model:<name>` scope stays strictly global.
3. `state_control/projector.py` SHALL project org-model switches (with no credential prefix) to the
   new scope; the "NOT enforced (GW29)" note SHALL stop being emitted for them, and the
   `unsupported_switches` tally SHALL fall to zero for that case. A credential-prefix switch
   (per-key scope) remains unsupported and stays refused-and-noted (D5).
4. The `on_count` attestation SHALL include the new scope, and the manifest-drift check SHALL cover it.
5. Rollout safety: a gateway built before GW29 treats an unknown scope as UNAVAILABLE (fail closed).
   The design SHALL state the deploy order (gateways before projector) and a flag or version gate so
   an older fleet is never knocked into a global 503 by a newer projector.

### 5.4 Isolation and reroute as plan state (R4)

1. Isolation state (isolated model, fallback model, reason, `expires_at`, origin: operator or auto)
   SHALL be part of the signed state the gateway reads, not a request-time lookup of control.
   Decision D3: carried in the KS-kind record body as additive optional fields, unless the design
   demonstrates a new `StateKind` is necessary; `scope_is_on` semantics for `on` are unchanged.
2. Where a request resolves to an isolated model: if a fallback is configured and eligible, the request
   SHALL be rerouted and the routing decision SHALL carry reroute provenance (isolated model,
   fallback, decision source `isolation`, reason). If no fallback is configured: a declared refusal.
3. A fallback SHALL be eligible only if it is (a) not killed at any scope, (b) not itself isolated
   (no chaining; a single hop, D6), (c) permitted by the org's allowed models, (d) within the
   request's compliance and sensitivity constraints. Otherwise: a declared refusal with its own
   posture code, never a silent second hop, never the original isolated model.
4. Reroute SHALL occur before dispatch and before the first byte (consistent with the no-splice
   contract in `edge/stream_control.py`). A mid-stream reroute SHALL NOT occur.
5. Zero requests reach an isolated model's provider after the isolation is visible in RAM, including
   retries and fallbacks inside `run_with_no_splice`. The isolated model is excluded from every
   attempt, not only the first.
6. Routing must stay a pure function of its inputs (`dispatch/routing.py` R8.3: no clock, no
   randomness, no module state). Isolation inputs are passed in as values.
7. GW28 is the model-routing dependency (plan model fields, per-org allowed models). GW29 SHALL use
   whatever GW28 exposes at merge time; if a needed field is absent, the design SHALL define the
   narrowest additive field and record it as a GW28 handoff (D7).

### 5.5 Risk score and auto-isolation (R5)

1. A per-model risk score in the range 0.0 to 1.0 SHALL be computed from provider errors (by class:
   timeout, 5xx, rate limit, malformed upstream) and guard outcomes (blocks and output-guard
   hits attributable to the model), over a sliding window, with a minimum sample count before a
   score is produced.
2. The score SHALL be exposed through a read endpoint or metrics series suitable for the console's
   5-second gauge, with cardinality bounded by construction (top-K + other, as in GW14d; no
   per-tenant label). Read cost O(models), RAM only.
3. Auto-isolation SHALL trigger when the score crosses the threshold with the sample minimum met. The
   threshold, window, minimum sample count, isolation duration and recovery probe cadence SHALL be
   derived in `runtime/resources.py` (no literal elsewhere), with working defaults in D8, chosen so
   an injected 50% provider error rate trips within the declared window (L29-3).
4. Auto-isolation tightens only: it can add an isolation, never remove a kill switch or an operator
   isolation. A process-local auto-isolation takes effect immediately in the detecting process
   (D9), is emitted as an event for control to persist as signed state, and converges fleet-wide
   when the signed record lands.
5. Every isolation carries `expires_at`. Expiry SHALL be evaluated on the request path from the
   record, with the injected monotonic/wall clock, on every read; the request path SHALL NOT keep
   treating an expired isolation as active (the 23 July defect), and SHALL NOT keep an expired
   isolation in a display surface. Display surfaces and the request path read the same state, so
   they agree within one refresh (L29-4).
6. Recovery: after expiry the model is eligible again; if a probe policy exists it is capped by the
   risk threshold, so a still-failing model re-isolates rather than flapping traffic. Hysteresis
   (re-isolation after recovery requires fresh samples, not stale window data) SHALL be specified.
7. An engage of an operator isolation SHALL win over an auto-recovery: auto-logic never clears an
   operator-origin isolation.

### 5.6 Notification (R6)

1. Isolate, reroute and recovery SHALL each produce one structured event (type, org or global scope,
   model, fallback, reason, origin, request-free, no content) through an injected `Notifier`
   protocol defined in a lower layer and implemented by `edge` or by control.
2. Reroute events are rate-limited per (model, fallback) so a hot reroute does not produce one
   event per request; the counter still increments for every reroute (as `edge/event_log.py` does).
3. Notification failure or a slow notifier SHALL NOT block, delay or weaken enforcement, SHALL be
   counted and logged at WARNING, and SHALL NOT be retried on the request path.
4. Delivery transport (email, webhook, pager) is control/console work; GW29 owns the event, the seam,
   the counter and the audit record, and provides an in-process test notifier.

### 5.7 Observability and audit (R7)

1. New series SHALL be label-free producers published through the GW14d Publisher with a fixed
   `SERIES_COUNT` (the Publisher asserts rendered count equals declared count): engaged-scope
   counts, refusals by closed reason, in-flight cuts by cause, isolated-model count, reroutes,
   auto-isolations, notifier failures, kill-switch snapshot age, and per-model risk via top-K.
2. A kill-switch refusal, an in-flight cut, a reroute and an isolation change SHALL each produce an
   audit record with reason and no evidence payload (R2-05 item 6).
3. `edge/series_count.py` and the closed rejected-status vocabulary (`errors.RENDERABLE_STATUSES`)
   SHALL be extended consistently; an out-of-vocabulary code counts on the vocab-error counter
   and adds no series (N1).

## 6. Non-functional requirements

- **N1 Hot path.** The admission check is O(1) RAM (set membership, one lock acquire) with no
  allocation proportional to tenant count; it SHALL not move the C4 / 20 ms budget measurably.
  A micro-benchmark SHALL show the added per-request cost and per-chunk cost.
- **N2 Per-chunk cost.** The in-flight source runs at each chunk boundary. It SHALL be O(1) and take
  no store call, no I/O, no scan of the engaged set.
- **N3 Fail closed.** Every error path in this card refuses or cuts; `FAIL_OPEN` stays pinned at 0.
  No `except Exception: pass` on any enforcement path.
- **N4 Layering.** Bottom-up under the import-linter contract:
  `edge > admit > plan > detect > resolve > dispatch > egress > audit > runtime > contracts > domain`
  (`gateway_v2/pyproject.toml` `[tool.importlinter]`). Value types and posture codes in `domain`,
  derivations in `runtime/resources.py`, pure logic in `admit` / `dispatch`, wiring and HTTP in
  `edge`. `egress` never imports `detect`. No HTTP framework outside `edge`. `resolve` imports only
  `domain`. `state_control` and `audit_control` sit outside `root_packages` and may import only
  `gateway_v2.domain`.
- **N5 Capacity literals.** No capacity literal (thresholds, windows, counts, durations, queue
  sizes) outside `runtime/resources.py`. New knobs there are derivations of existing contract
  fields where possible and fail closed when unset.
- **N6 No module-level mutable state.** Per-process state lives on injected instances. No
  module-level dict, list, set or cache; constants are immutable (`MappingProxyType`, tuples,
  `frozenset`).
- **N7 Value types.** New value types are `@dataclass(frozen=True, slots=True)`. Classes holding
  mutable state use `__slots__`.
- **N8 Determinism / injection.** Clocks, random sources, the store client, the notifier and the
  spawn seam are injected. Tests use an injected clock; no sleeps for time.
- **N9 Thread safety.** The snapshot is read from the event loop and written from the synchroniser
  task and possibly a thread; every new shared structure is guarded the same way as
  `KillSwitchSnapshot` (lock around state, immutable reads).
- **N10 Secrets and content.** No prompt, completion, key or credential appears in an event, audit
  record, log line or metric label.
- **N11 Typing and lint.** `mypy --strict` and `ruff` clean on every touched file, in `gateway_v2`
  and in `state_control` if touched.

## 7. Acceptance criteria

EARS form where it fits. "Visible" means applied in the process snapshot. "Bound" means
`RV_STATE_FRESH_MS` (default 5 000 ms) plus the stream cut bound `max_cut_latency_s()`.

### Admission

- **AC1.** WHEN the global switch is engaged and visible, THE gateway SHALL refuse every new request
  with the declared kill posture code before quota is charged and before any provider call.
- **AC2.** WHEN `org:A` is engaged, THE gateway SHALL refuse org A's new requests and SHALL admit
  org B's unchanged (no cross-tenant effect), in the same process, on the same snapshot.
- **AC3.** WHEN `model:M` is engaged, THE gateway SHALL refuse any request that routes to M, for
  every org, and SHALL NOT refuse requests routed to other models.
- **AC4.** WHEN the kill-switch snapshot is STALE, never loaded, or drifted from the attested
  `on_count`, THE gateway SHALL refuse new requests with `KILL_SWITCH_UNAVAILABLE` (503) and SHALL
  NOT treat absence as off.
- **AC5.** A refused request SHALL charge zero budget tokens and consume zero lease, and SHALL
  produce exactly one audit record carrying a closed-vocabulary reason.
- **AC6.** WHEN the OFF record for a scope arrives and becomes visible, THE gateway SHALL admit
  again with no restart. WHEN a switch is engaged then disengaged within one refresh, THE final
  state SHALL equal the last signed record.
- **AC7.** The org id used for the org check SHALL be the resolved principal's, and a request that
  presents another org's id in a header or body SHALL NOT bypass or trip the check.

### Mid-stream

- **AC8.** GIVEN a stream admitted before the engage, WHEN its org (or global, or its model) switch
  becomes visible, THEN the stream SHALL be cut at the next chunk boundary with a terminal
  `STREAM_KILLED` error frame, the provider SHALL be aborted, and no further upstream bytes SHALL
  reach the client. (G-17: 2 000-token stream, engage at +2 s.)
- **AC9.** GIVEN a stream, WHEN its key is revoked and the revoke is visible, THEN it SHALL be cut
  with `STREAM_KEY_REVOKED` at the next chunk boundary.
- **AC10.** WHEN the kill-switch snapshot goes STALE or unavailable mid-stream, THEN the stream SHALL
  be cut with `STREAM_SNAPSHOT_STALE`, and this SHALL dominate other signals (existing precedence).
- **AC11.** A stream of org B SHALL NOT be cut by an engage of org A. A stream on model N SHALL NOT be
  cut by an engage of model M.
- **AC12.** `_NoControlSource` SHALL NOT be the default of any production-profile code path;
  a test SHALL fail if `kill_switch_engaged` can only ever be `False` in the production boot.
  The dev/test profile MAY keep an explicit, named no-control source.

### Scopes and projector

- **AC13.** An engaged org-plus-model switch SHALL refuse only that org's requests routed to that
  model; the same model for another org SHALL still be served.
- **AC14.** The projector SHALL write the org-model scope as ON/OFF records with explicit OFF, SHALL
  keep `on_count` correct, and SHALL stop reporting that case as unsupported. A per-key (credential
  prefix) switch SHALL still be reported as unsupported and counted, not silently dropped.
- **AC15.** A pre-GW29 snapshot reading a record of the new scope SHALL fail closed (existing
  behaviour); a GW29 snapshot reading only old scopes SHALL be unchanged. A round-trip test SHALL
  cover both.

### Isolation and reroute

- **AC16.** WHEN model M is isolated with an eligible fallback F, THEN requests that would route to
  M SHALL be served by F, M SHALL receive zero provider calls (including retries), and the routing
  decision SHALL name the reroute (isolated model, fallback, source `isolation`, reason).
- **AC17.** WHEN F is killed at any scope, OR is itself isolated, OR is outside the org's allowed
  models, OR violates the request's compliance or sensitivity constraint, THEN the request SHALL
  be refused with a declared posture code distinct from the plain kill code, and SHALL NOT reach M,
  F, or any third model.
- **AC18.** WHEN M is isolated and has no fallback, THEN the request SHALL be refused with a declared
  code.
- **AC19.** Routing SHALL remain pure: two calls with equal inputs return equal outputs; a property
  test over at least 10 000 seeded random cases SHALL assert determinism and that no output ever names
  an isolated or killed model.
- **AC20.** No reroute SHALL occur after the first content byte; a failure after the first byte
  closes the stream with the declared error frame (existing no-splice rule).

### Risk, auto-isolation, expiry

- **AC21.** The risk score for a model SHALL be 0.0 below the minimum sample count, SHALL be monotone
  non-decreasing in the error ratio at fixed sample count, and SHALL be within [0.0, 1.0] for all inputs.
- **AC22.** GIVEN a sustained 50% provider error rate on M, THEN M SHALL auto-isolate within the
  declared window and a notification event SHALL be produced and traffic SHALL flow to the fallback (L29-3
  local equivalent with injected clock and fake provider).
- **AC23.** A 10% error rate (below threshold) SHALL NOT isolate; a burst smaller than the minimum
  sample count SHALL NOT isolate.
- **AC24.** Auto-isolation SHALL NOT override, shorten or clear an operator-origin isolation or any
  kill switch.
- **AC25.** WHEN `expires_at` passes, THEN the request path SHALL stop treating the isolation as
  active on the next read, AND the console-facing read model SHALL stop reporting it active within one
  refresh. A test with an injected clock jumping past expiry SHALL prove both surfaces agree
  (regression for the v1 23 July defect). A missing, malformed or non-numeric `expires_at` on an
  auto-origin isolation SHALL be treated as expired-at-creation plus the derived default duration, not
  as "never expires"; on an operator-origin isolation as "no expiry" only if explicitly encoded as such
  (D10).
- **AC26.** After recovery, re-isolation SHALL require samples collected after recovery (no flapping
  from stale window data).

### Notification and observability

- **AC27.** Isolate, reroute and recovery SHALL each emit exactly one event on the transition; a
  sustained reroute SHALL emit at most one event per rate window per (model, fallback) while the
  counter counts every reroute.
- **AC28.** A notifier that raises or blocks SHALL NOT change the enforcement outcome of the request that
  triggered it, SHALL add at most a bounded cost to that request, and SHALL increment the failure counter.
- **AC29.** The Publisher's rendered series count SHALL equal its declared `SERIES_COUNT` after adding
  the GW29 producers, SHALL be invariant to tenant count, and SHALL be invariant to the number of
  models beyond top-K (property test, 10 000 iterations).
- **AC30.** No event, audit record, metric label or log line SHALL contain request content, a key or a
  credential (assertion over captured output).

### Layering and hygiene

- **AC31.** `import-linter` SHALL pass all contracts with no new ignore. `mypy --strict`, `ruff`, and
  the five AST gates (`capacity_literals`, `no_module_mutable`, `frozen_dataclasses`,
  `http_outside_edge_resolve`, `tenant_scale`) SHALL exit 0 over `gateway_v2` (and over `state_control`
  where touched), apart from the documented pre-existing R2-05 `state_control/valkey.py:100` finding.
- **AC32.** A negative control SHALL show that reverting the admission consult, or restoring
  `_NoControlSource`, makes the acceptance tests fail.

## 8. Offline gate obligations

The card does not exit on unit tests alone. The local half is closed offline; the live half is
declared open with named gates (house convention, e.g. R2-06, R2-07, GW12).

**Required offline evidence**

1. New tests under `gateway_v2/tests/admit/`, `tests/edge/`, `tests/dispatch/`, `tests/runtime/` (and
   `tests/state_control/` if the projector changes), prefixed `lgw29`, one file per area.
2. Correctness properties as seeded `random.Random` tests of at least 10 000 iterations (house idiom,
   no `hypothesis`, `asyncio.run`), covering at minimum: (P1) engaged global/org/model never admitted;
   (P2) other tenants and other models unaffected; (P3) stale or drifted never admitted; (P4) routing
   never names a killed or isolated model; (P5) reroute never crosses compliance or sensitivity;
   (P6) expired isolation never active on the request path or in the read model, and they agree;
   (P7) risk score bounds and monotonicity; (P8) cut lands within the bound for any engage time;
   (P9) refused request charges nothing; (P10) fixed series count.
3. L29 local equivalents with an injected clock and fake provider: L29-1 (engage under load, new
   requests refused within the bound, streams cut), L29-2 (isolate with fallback, zero provider calls
   to M), L29-3 (50% error injection, auto-isolation in window, notification, recovery on fallback),
   L29-4 (expiry, surfaces agree within one refresh). G-17 local equivalent (2 000-token stream,
   engage at +2 s) with the cut asserted.
4. Projector tests including the new scope, explicit OFF, `on_count`, and the unsupported-per-key
   path; with a live Postgres 16 (`AMF_PG_DSN`) when available, otherwise declared skipped with reason.
5. Old-gateway / new-projector compatibility test (AC15).
6. Micro-benchmark of the admission and per-chunk check cost (N1, N2) recorded in evidence.
7. Full offline suite: no failures and no new skips relative to the count measured at the start of
   the work on this branch (record the baseline number). Run `mypy --strict`, `ruff`,
   `import-linter`, and the five AST gates, and record the outputs.
8. Negative controls per AC32 recorded in evidence.
9. Evidence under `docs/plans/evidence/<date>-gw29/`; a plan under `docs/plans/<date>-gw29-*.md`.
10. Handoff: a GW29 changelog entry in `AGENTS.md` and a status update to the progress doc card
    table, stating what is closed locally and what is open.

**Declared OPEN (deferred live or cloud gates, not closable locally)**

- Live L29-1..L29-4 on the real stack and fleet, with real provider error injection.
- Real notification delivery (transport belongs to control/console).
- Fleet-wide convergence timing of a signed auto-isolation record across replicas.
- Console 5-second gauge and display-surface agreement in a browser (GW35 / UI cards).

## 9. Recorded decisions (conservative, fail-closed)

| ID | Ambiguity | Decision | Why |
|---|---|---|---|
| D1 | Section 0.7-1: continue or cut in-flight streams? Owner has not signed. | CUT at the next chunk with the terminal error frame, for kill (global, org, model), key revocation, and stale snapshot. | Matches the owner's 2 Nov working default; the safe direction; the seam and codes already exist. A signed reversal changes only a policy switch, so the design SHALL keep the cut behind one named setting and not scatter it. |
| D2 | Should isolation cut in-flight streams? | NO. Isolation is a routing/health action; running streams on the isolated model finish unless the model is also killed. Applies to new requests only. | Isolation is not a security stop; cutting healthy streams on a degraded model adds harm. Kill remains the stop. Recorded so the owner can flip it. |
| D3 | Where does isolation/reroute state live? `ExecutionPlan` has no model fields; there are four `StateKind`s. | Additive optional fields in the signed KS-kind body for model-scoped isolation (fallback, reason, origin, `expires_at`); no new `StateKind` unless the design proves it necessary. | Reuses the signed, attested, freshness-gated path already proven by GW05/GW36; avoids a fifth kind, manifest and applier. |
| D4 | Per-org model switch has no scope; `model:` is global. | Add an additive org-plus-model scope. NEVER map org-model onto `model:`. | Mapping it globally would stop a model for all tenants (explicit projector warning). |
| D5 | Credential-prefix (per-key) switches. | Remain unsupported: refused-and-noted by the projector, counted, not enforced. Out of scope for GW29 unless the design shows trivial parity. | No v2 per-key scope; per-key stop is already covered by key revocation (AC9). |
| D6 | Fallback chains. | Single hop only. A fallback that is killed or isolated is a declared refusal. | Runbook text; avoids loops and unbounded routing. |
| D7 | GW28 field availability at merge. | Use GW28's surface; add only the narrowest additive field if missing and record a GW28 handoff. | Parallel cards; avoid blocking. |
| D8 | Threshold, window, minimum samples, isolation duration, probe cadence values. | Derived in `runtime/resources.py`; working defaults chosen so 50% errors trips and 10% does not; flagged OWNER-TO-SIGN, not signatures. | No capacity literal elsewhere; L29-3 requires 50% to trip. |
| D9 | Workers cannot write signed state; auto-isolation needs fleet effect. | Process-local immediate isolation (tightening only) plus an emitted event for control to persist as signed state; converges on the signed record. | Fast local protection; no gateway writes to the control state; fail-closed direction only. |
| D10 | Missing or malformed `expires_at`. | Auto-origin: treated as expired after the derived default duration from creation. Operator-origin: "no expiry" only if explicitly encoded. A malformed value makes the record's isolation UNAVAILABLE for that model, which refuses that model's traffic (never "active forever" silently, never "inactive" silently). | Closes the 23 July defect class both ways. |
| D11 | `plan_changed` in-flight signal. | Out of scope. The real source reports it as not evaluated and the design says so explicitly; it is not wired to a constant `False` without a comment and test. | The owner decision covers kill and revocation only; plan-change cutting is a separate policy. |
| D12 | Notification transport. | Event, seam, counter, audit only. Delivery is control/console. | Not a gateway layer concern; keeps admit free of I/O. |
| D13 | Rollout order for the new scope. | Gateways first, then projector enabling, guarded by a flag or version gate; design states the sequence. | Old gateways fail closed globally on an unknown scope, which would turn a deploy into an outage. |

## 10. Out of scope

- Console UI (gauge, buttons, display surfaces): UI cards and GW35. GW29 provides the data and the
  agreement guarantee.
- Control-side (Django) models, APIs and notification delivery, beyond the `state_control` projector
  and writer changes strictly needed for the new scope and isolation records.
- Per-key kill scope (D5), plan-change cutting (D11), cross-compliance reroute policy authoring (GW28
  owns routing policy fields).
- Provider-side health probing infrastructure; GW29 consumes errors the gateway already observes.
- Fleet-scale and live-cloud certification (declared open in section 8).
- Changing `KillSwitchSnapshot` freshness values (`FRESH_MS` stays 5 000) or the holdback, admission
  or quota mechanisms except to call them in the specified order.
- Rewriting the v1 gateway path (`main.py`, `circuit_breaker`); v1 behaviour is reference only.

## 11. Grounding: files found

- Kill-switch snapshot: `gateway_v2/gateway_v2/admit/killswitch.py`; export list `admit/__init__.py`.
- Hard-coded false: `gateway_v2/gateway_v2/edge/app.py:769-790` (`_NoControlSource`), defaults at
  `edge/app.py:237,357,386`, `edge/serve.py:204`; handler field `edge/routes.py:264`, use `:489`.
- In-flight seam: `gateway_v2/gateway_v2/edge/cancel.py:329-480`; stream wiring `edge/routes.py:476-520`,
  `edge/asgi_sse.py:207`.
- State runtime (snapshot built and fed): `gateway_v2/gateway_v2/edge/state_runtime.py:183,205,389`;
  production boot note `edge/production_profile.py:8,492`.
- Feed writers (GW36 S2/S3): `gateway_v2/state_control/projector.py` (ON/OFF phases, unsupported
  note), `state_control/writer.py:239` (`killswitch`), `state_control/pg.py` and `schema.py`
  (`on_count`), `state_control/source_pg.py` (`core_killswitch` read), `state_control/rehydrate.py:336`.
- Feed plumbing: `runtime/state_feed.py`, `runtime/state_sig.py`, `runtime/store_keys.py`,
  `domain/state.py`, `domain/locks.py` (`FRESH_MS`).
- Posture / errors: `domain/posture.py` (`KILL_SWITCH_UNAVAILABLE`, `STREAM_*`), `edge/errors.py:269`.
- Routing: `dispatch/routing.py` (`DispatchRouter`), `dispatch/authorized.py:269`,
  `dispatch/guard_routing.py`, `dispatch/provider_errors.py`; plan type `domain/plan.py:89`.
- Resource derivations: `runtime/resources.py` (`max_stream_duration_s`, `max_cut_latency_s`,
  `max_snapshot_age_s` at lines 349-355).
- Layer contract: `gateway_v2/pyproject.toml` `[tool.importlinter]` (lines 149-215).
- Related prior spec for the v1-side honesty: `.kiro/specs/routing-isolation-honesty/requirements.md`
  (v1 pipeline; reference for the reroute and "dead alias" lessons, not an implementation target).
- Spec templates: `.kiro/specs/gw36-s4-admission-quota/`, `budget-lease/`, `admission-control/`.
