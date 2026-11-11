# GW36 S3 — Gateway state runtime, hashed identity, 401/503 split (plan)

Status date: 3 November 2026. Branch `suraj-revamp`. Parent plan:
`docs/plans/2026-11-02-gw36-production-boot.md` §9.4 row S3 (G3, G4, G5, N7–N11), plus N18 and N19
from S2's review. Nothing below is implemented until the reviews in §9 have challenged it.

## 1. Goal and exit evidence

The gateway reads the signed state S2 now writes, and resolves real console keys.

Exit evidence (live, on the VM stack). `AMF_PROFILE=production` only: the rest of production boot
is S4/S5.

| # | Check | Expected |
|---|---|---|
| E1 | A console-created key | Passes identity and plan. It is refused at the next unwired stage (admission) with that stage's declared code, never at identity |
| E2 | An unknown, revoked or missing key | HTTP 401 `invalid_api_key`, streaming and non-streaming; the official SDK raises `AuthenticationError` |
| E3 | A key deactivated in the console | 401 within the projector lag plus one refresh |
| E4 | Valkey stopped | A valid key gets 503 `shared_state_unavailable`, never 401; `/readyz` 503; `/livez` 200 |
| E5 | Valkey back | Recovers by itself (the state task survives) |
| E6 | Stamp absent (re-hydrators stopped) | 503 once the stamp ages past `FRESH_MS`, never 401 |
| E7 | `/metrics` | 200 throughout; the 401 is counted on `rejected{status="401"}` |

## 2. Facts (verified, 3 Nov)

| # | Fact | Evidence |
|---|---|---|
| F1 | Production boot wires `fail_closed_resolver`; no `IdentityCache`, `StampView`, `StateSynchroniser` or store client is built anywhere in product code | `edge/serve.py:237`; grep |
| F2 | `CachingIdentityResolver.resolve` collapses absent credential, unknown/revoked key and unverified state into `None`; about ten call sites in six files render 503 `shared_state_unavailable` | `edge/identity_resolver.py:225-236`; `openai_chat.py:547`, `openai_misc.py:415,753`, `openai_misc_models.py:109,136`, `openai_responses.py:223,131`, `mcp.py:205` |
| F3 | `bearer_key` returns the raw token; control keys records by `sha256(raw).hexdigest()` | `identity_resolver.py:135-162`, `GatewayAPIKey.hash_raw_key` |
| F4 | A streaming identity refusal is SSE 200 plus a generic error frame; no request id, no audit | `routes.py:392-399`, `audit_emit.py:74` |
| F5 | No `invalid_api_key` code; 401 already in `RENDERABLE_STATUSES` (via `stream_key_revoked`) | `errors.py:164,276-278` |
| F6 | `ValkeyStateStore` converts no error; raw redis errors escape `round_once`, `drain` and `drain_all` (aborting the other kinds); `observe_stamp` is unguarded | `store_valkey.py:106-163`, `state_task.py:132-221`; the live drill asserts the raw escape (`test_lgw05c_drills.py:409`) |
| F7 | A KEY round of more than 32 records runs `identity_applier` in a thread; `IdentityCache` has no lock (N8) | `state_task.py:144-147`, `admit/identity.py:113-115` |
| F8 | N18: a fill that read an active record before a revoke delta is cached forever (`_admit` checks only "newer than applied") | `admit/identity.py:189-195,241-251` |
| F9 | `build_app(state_view=)` is honoured only with a `MetricsDirectorySource` registry; the production boot passes a plain registry, so the stamp gate would silently not exist | `app.py:672-707`, `serve.py:105-122,234` |
| F10 | `/readyz` == `/health`; no `/livez`; compose healthcheck uses `/readyz` | `app.py:531-533`, `docker-compose.yml:533-538` |
| F11 | Freshness is one global stamp; a worker that has not yet caught up a kind answers `PlanUnknownTenant` → `plan_unavailable` 503 for orgs not applied (N9, a 503 not a 403 in v2) | `plan/snapshot.py:65-91`, `openai_chat.py:573-577` |
| F12 | `ReplicaSnapshot._served` survives an offboard (N19) | `plan/delta.py:97-100`, `plan/snapshot.py:81-84` |
| F13 | Construction must be side-effect-free: `main()` assembles the app once in the parent as a probe; each worker rebuilds; background work starts from the lifespan (the `DevServedApp` pattern) | `serve.py:468-502,693-699`, `dev_admit.py:302-355` |
| F14 | uvicorn `lifespan` defaults to `"auto"`; the wrapper must catch its own startup errors and fail closed rather than rely on uvicorn | `serve.py:304-314` |

## 3. Design

### 3.1 Identity outcome (G4, F2, F5)

- New posture code `invalid_api_key` → HTTP 401 `authentication_error`, with OpenAI's message
  shape ("Incorrect API key provided."). The key is never echoed. It has an `ERROR_SHAPES` entry
  and is deliberately not in `POSTURE_CODES`: a bad credential is a client fault, not a gateway
  posture. It is counted by `rejected{status="401"}`.
- `IdentityOutcome`, a value type: `resolved(identity)`, `absent` (no or empty bearer),
  `unknown` (the store verified that no live record exists) and `unverified(reason)` (stale stamp,
  store down, kind not caught up). The first three map as follows: resolved proceeds; absent and
  unknown become 401 `invalid_api_key`. `unverified` becomes 503 `shared_state_unavailable`.
- `IdentityResolver` gains `resolve_outcome(scope) -> IdentityOutcome`. A module helper
  `identity_of(resolver, scope)` prefers it and falls back to `resolve()` (`None` → 503) for any
  resolver that predates it. All the call sites use the helper. `resolve()` is kept, so no
  external caller breaks.
- `CachingIdentityResolver` and `DevStaticIdentity` implement `resolve_outcome`. A wrong dev key
  becomes 401 too, for parity with production. `fail_closed_resolver` stays `None`, so the
  default boot is unchanged at 503.

### 3.2 Hashed key and the store fetch (G3)

- `hashed_bearer_key(scope)` = `sha256(bearer).hexdigest()`. The raw token is never logged,
  cached or stored.
- `state_key_fetch(store, secret)`: one `records(KEY, (hash,))` round trip, then
  `decode_record`, then `principal_of`. An absent record is `None` (unknown); a deleted record is
  `None` (revoked); store and integrity failures are `StoreDataUnavailable` (unverified).

### 3.3 Cache correctness (N8, N18)

- N18: `on_delta` marks keys that have a fill in flight. A marked fill is discarded and fetched
  again, once. If it is marked again, it raises `StoreDataUnavailable` (503) rather than serve
  either answer. The marks are bounded by the in-flight set.
- N8: `StateSynchroniser(inline_kinds={KEY})` never offloads the KEY applier. PLAN and KS
  appliers already lock.

### 3.4 Store errors and supervision (N7, F6)

- `ValkeyStateStore` converts `redis.RedisError`, `OSError` and `asyncio.TimeoutError` into
  `StoreDataUnavailable("state store unreachable: ...")` at each call. The redis error classes
  are discovered lazily, as `state_control` does, so `runtime` keeps no hard redis import at
  module scope.
- `drain_all` guards `observe_stamp`: a stamp read failure leaves the view as it was (it ages out)
  and the kinds are still drained.
- The runtime loop catches any other exception per cycle, logs it, counts it and continues. It
  never dies. The live drill that asserted the raw escape is updated to assert
  `StoreDataUnavailable`.

### 3.5 Per-kind readiness (N9, F11)

- `KindGate(stamp, runtime, kind)` satisfies the existing `fresh()` / `unverified()` surface. It
  is ready only when the global stamp is fresh, and this process has completed a non-truncated
  drain of that kind since it started, and the last good round of that kind is within `FRESH_MS`.
- `IdentityCache` gets the KEY gate, `ReplicaSnapshot` the PLAN gate and `KillSwitchSnapshot` the
  KS gate. Their constructor type widens from `StampView` to a small `FreshnessGate` protocol in
  `runtime/state_stamp.py`, which `StampView` already satisfies.
- `runtime.ready()` = all three gates plus the stamp.
- N19: on an offboard, `PlanDeltaApplier` tells the snapshot to forget the served plan.

### 3.6 The state runtime and its lifespan (G5, F13, F14)

- New `edge/state_runtime.py`. `StateRuntimeConfig.from_env`: `AMF_STATE_VALKEY_URL`,
  `AMF_STATE_HMAC_KEY` (required, same names as `state_control`), `AMF_STATE_NAMESPACE`,
  `AMF_STATE_FRESH_MS`, `AMF_STATE_REFRESH_MS` → `StateKnobs`.
- `StateRuntime` construction builds every object (client unconnected; `redis.asyncio` connects
  lazily) and performs no I/O.
  - The objects: `ValkeyStateStore`, `FeedReader`, `StampView`, the three gates, `IdentityCache`,
    `PlanStore`, `ReplicaSnapshot`, `KillSwitchSnapshot`, `StateSynchroniser` and
    `StateMetricsRecorder`.
  - The client is bounded with `socket_timeout = store_timeout_ceiling_s / 2` and checked with
    `require_bounded_client`.
- `startup()` spawns one task. It runs `bootstrap_engaged(KS)` once, then loops: `drain_all`
  (guarded), metrics, then waits `next_delay_s` or a nudge event. A `NudgeListener` task on the
  updates channel only sets the event. One task owns every drain, so there is no cursor race.
- `shutdown()` cancels both tasks and closes the client. Startup failures are logged and leave
  the gates unready, so the gateway fails closed with 503 and still starts.
- `StateServedApp` wraps the inner app like `DevServedApp`, intercepting only `lifespan`.

### 3.7 Streaming identity refusal and request id (N11, F4)

- An identity-stage refusal renders as plain HTTP whatever the `stream` flag, as OpenAI does for
  401: the SDK raises `AuthenticationError` before any stream starts.
- It gets a minted `x-request-id` (`zs-` plus 24 random hex characters). That id is unrelated to
  the request content. The S1 finding that the post-identity request id hashes the raw input is
  deliberately left for S5, where the id scheme is replaced with the audit and console contract
  (GW35), so it is not changed twice.

### 3.8 Health endpoints (N10, F9, F10)

- `/livez`: static 200, no readiness call. The compose healthcheck for gateway services moves to
  it; a gateway that has not yet seen a stamp is alive but not ready.
- `/readyz` and `/health` keep readiness semantics. The production boot passes
  `readiness=runtime.ready` explicitly (F9), so the gate cannot silently vanish.

### 3.9 Boot selection

- `AMF_PROFILE=production`:
  - The state runtime is built; it is required, so the gateway refuses to start without the
    state URL and HMAC key.
  - Identity becomes the hashed `CachingIdentityResolver`, and `snapshot_source` becomes the
    runtime's `ReplicaSnapshot`.
  - Readiness is `runtime.ready`, and the app is wrapped in `StateServedApp`.
  - Every other dependency stays on its shipped fail-closed default, and the boot log names each
    as "pending S4/S5".
- `AMF_PROFILE` unset keeps today's table exactly. `AMF_DEV_PROFILE=1` is unchanged, except for
  the dev identity's 401.
- `AMF_PROFILE` with any other value, or together with `AMF_DEV_PROFILE=1`, refuses to start.

## 4. Not in S3

Quota, lease and admission (S4). Provider, catalogue, the per-org detector seam and the full
production boot with L36-1 (S5). The live state-metrics adapter in the Publisher (N14, S5). The
request-id scheme (S5/GW35). Kill-switch enforcement on the request path (GW29); S3 only keeps the
KS snapshot current.

## 5. Tests (house idiom: seeded `random.Random`, at least 10,000 iterations, seed in messages)

1. Identity outcome mapping, at every call site and for streaming and non-streaming:
   absent/unknown → 401, unverified → 503, resolved → proceeds.
2. A hashed namer property: the cache key is never the raw bearer, and the bearer never appears
   in logs or the cache.
3. N18 property: random interleavings of fills and deltas. A key revoked by an applied delta is
   never served afterwards.
4. N8: a KEY round over the offload threshold runs inline.
5. N7: injected redis errors on every store call become `StoreDataUnavailable`; one kind's fault
   does not stop the others; the loop survives.
6. `KindGate`: not ready until a non-truncated drain; ages out; ready again after a good round.
7. N19: offboard, then re-register, never serves the old plan.
8. Runtime lifespan: startup failure → unready but serving; shutdown cancels and closes.
9. Boot table: production requires the state env; an invalid `AMF_PROFILE` refuses; the default
   boot is unchanged.
10. Update the tests that pin 503 for a wrong key (dev profile and the default-boot identity
    tables stay 503 where the resolver is `fail_closed_resolver`).

## 6. Live check (VM stack)

A second gateway container, `AMF_PROFILE=production`, on the stack network, published on
`127.0.0.1:28302`. It runs E1–E7 using console keys created through the API, the official
`openai` SDK for the 401, and docker stop/start of my own Valkey and re-hydrators.

## 7. Estimate

2–3 agent-days.

## 8. Risks

- R1: widening the freshness type touches `admit/`, `plan/` and `runtime/`. Import direction is
  admit/plan → runtime, which is allowed.
- R2: about 25 tests pin today's identity 503. Each one is classified: fail-closed boot stays
  503; a wrong-key-with-a-real-resolver test becomes 401.
- R3: the dev profile's wrong key changes from 503 to 401 (a behaviour change, intended).
- R4: plain-HTTP identity refusal on streams changes the SSE preservation tables.

## 9. Review outcome (supersedes §3–§6 where they disagree)

Five contradictory reviewers attacked the plan. F1–F14 held; F9 is narrower than worded: only
`/readyz` loses the gate, because the request-path gates are their own stamps. Each finding below
was re-checked in code.

| # | Finding (reviewer) | Accepted change |
|---|---|---|
| R1 | OpenAI's real 401 is type `invalid_request_error`, code `invalid_api_key`; the house 401 (`stream_key_revoked`) uses `authentication_error` (1) | Use OpenAI's type unless the gateway's own wire-contract gate pins type→status. Decided in code with the gate as evidence |
| R2 | Readiness is per worker but `/readyz` is probed per container; a cold worker flaps (2) | `startup()` waits, bounded by `FRESH_MS`, for the first good `drain_all` before the lifespan completes. uvicorn routes nothing to a worker before its lifespan startup finishes |
| R3 | A cold worker drains every KEY record just to evict an empty cache (2) | `fast_forward(KEY)`: read the head under the stamp floor and set the cursor to the manifest. Fills are fetched on demand |
| R4 | An error report has `truncated=False`; "good round" was undefined; a gate on wall time drifts (2, 3) | Good = `ok and not truncated`; the gate uses a monotonic clock |
| R5 | KS bootstrap before the stamp reads with floor START (2) | `observe_stamp()` first, then `bootstrap_engaged(KS)` |
| R6 | N18 marks must also discard a stale "absent" (a just-added key denied for 2 s) (2, 3) | Marks discard negative fills too |
| R7 | **A fill from a regressed store** (failover to a lagging replica) caches a revoked key with no TTL, and the revoke never re-appears in a delta (3) | The fetch reads the KEY manifest in the same round trip; a manifest below this worker's applied cursor or floor, or a record above the manifest, → `StoreDataUnavailable` (503) |
| R8 | Converting every `RedisError` hides WRONGTYPE / auth / config errors as a quiet 503 (2) | Convert only connection, timeout and OS errors. Anything else is logged at ERROR with its class and counted by the runtime loop; on the request path it is still a 503 |
| R9 | A second test pins the raw redis error: `test_lgw06_partition_regression.py:320-332` (2) | Updated with the drill |
| R10 | E7 cannot pass: the production boot has no observer and a static `/metrics` (4) | Production wires a Publisher with `observer` and a live state-metrics adapter. N14, dropping the duplicate re-hydrator family, is done if it fits the series contract, else declared |
| R11 | A 401 needs an `_ENVELOPE` entry or it renders 500 (4) | `_ENVELOPE` entry plus `ERROR_SHAPES` message; `WWW-Authenticate: Bearer` on every 401 (5) |
| R12 | Any distinct bogus token costs a Valkey read; the hash encoding was unspecified (5) | Only the console key shape (`[A-Za-z0-9_-]{48}`, control's `KEY_LENGTH`) is looked up; anything else is absent → 401, no store read. Hash `token.encode("utf-8")`, which equals ASCII for that shape. `Bearer` is read case-insensitively (RFC 7235) |
| R13 | A key resolved straight from the store can beat its org's PLAN through the feed (5) | Appliers ordered PLAN, KS, KEY. E1 accepts a 503 bounded by one refresh for a key minted after the last drain |
| R14 | Readiness includes the KS gate while KS is not enforced (5) | Kept on purpose: readiness means "state complete". The boot log says KS is not enforced (GW29) |
| R15 | `AMF_PROFILE` interplay (5) | Empty = unset; `production` plus `AMF_DEV_PROFILE=0` is allowed, plus `=1` refuses; exact match; raised in the boot factory so the parent probe refuses before the workers start |

Corrections found while implementing (evidence in the tests and the live run):

- **R1 settled.** The wire-contract gate keys status on the envelope `code`, not its `type`, and
  `invalid_api_key` is not in it. OpenAI's exact shape is used: 401 `invalid_request_error` /
  `invalid_api_key`.
- **The identity request id is minted at the call sites, not inside `errors.render_http`.**
  Putting it in the renderer broke the property that rendering is pure: the same code must give
  the same response. `identity_resolver.refusal_request_id(code)` is called by routes,
  embeddings, moderations, models and responses.
- **A store outage is bounded by `FRESH_MS`, per worker.** For up to 5 s a worker may serve what
  it verified. Each uvicorn worker has its own cache and gates, so a live drill must require
  consecutive agreeing answers; one answer says nothing about the other workers. The first live
  run showed exactly this, and the drill was fixed rather than the product.
- **Negative controls.** The N18 property fails on the old fill logic at iteration 1, and the
  N19 test fails without `forget`.

Declared, not fixed in S3:

- MCP `initialize`/`ping` answer before identity, and v1's `resource_metadata` discovery header is absent (GW32).
- Chat parses the body before identity, so an unauthenticated malformed body is 400, not 401.
- Plan pins grow per request id; they are dormant, with no caller (S4).
- Streams admitted before a revoke keep running (GW29).
- Failed-credential audit records (GW35).
- `/v1/models` with a bad key is 401 (OpenAI parity), where v1 was soft-auth.
- Rate 0 is not a GCRA (S4, N20).
