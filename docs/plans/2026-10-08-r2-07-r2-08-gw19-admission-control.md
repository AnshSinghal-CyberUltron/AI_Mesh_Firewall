# R2-07 / R2-08 (+ R2-18) / GW19 — Admission control, overload semantics, graceful drain

**Status:** implemented, local half CLOSED. **Card:** GW19.
**Severity:** HIGH (R2-07 / R2-08) / MEDIUM (R2-18). **Date:** 2026-10-08.
**Evidence:** `docs/plans/evidence/2026-10-08-r2-07-gw19/`.

---

## 1. Executive summary

Under overload the gateway's behaviour was **emergent and unbounded**: there was no admission
control at all. The queues grew without a declared ceiling, p99 collapsed, memory climbed, and the
system never recovered on its own. Round 2 measured the specific defects:

- **No admission control.** Nothing decided, per owner, whether a request should be served or shed
  before service began. Shedding, when it happened, was a side effect of exhaustion rather than a
  declared decision.
- **The gateway-side per-worker connection cap (GW03) broke the SLO under overload and never
  recovered** — a ≈20 ms floor from event-loop stalls that persisted after the burst passed.
- **A 12 ms instantaneous latency bound sheds long prompts first.** Admission that keyed off an
  instantaneous latency reading is biased against large prompts (prompt-size bias), shedding exactly
  the requests that cost the most to re-submit.
- **A tenant-blind FIFO queue let one tenant shed another 30/30.** With one global queue, a single
  saturating owner starved every other owner — there was no per-owner fairness.
- **Sheds carried a 6–11 ms Retry-After.** The OpenAI SDK honours that and retries twice, so each
  shed re-offered as load ≈2.6×, amplifying the overload the shed was meant to relieve.
- **Post-heal recovery took 10–180 s.** After a store partition healed, the standing backlog drained
  slowly and latency returned to the SLO only after tens of seconds to minutes.

The fix makes overload behaviour **explicit and bounded**: per-owner CoDel admission, admitted work
answered late and **never abandoned**, an explicit HTTP 503 overload response with a jittered
Retry-After ≥ 1 s and `x-should-retry: false`, bounded queues everywhere with exported depth and
age, a graceful drain state machine on SIGTERM, isolated-and-respawned worker crashes, slow-consumer
backpressure, and bounded post-heal latency recovery.

The component is built **bottom-up along the import-linter `layers` contract**
(`edge > admit > plan > … > runtime > contracts > domain`): `admit` sits directly below `edge` and
imports only `runtime` and `domain`. The pure CoDel math, the bound derivation, the shed verdict,
the drain state machine, and the metrics are all driven by an **injected clock** (`Callable[[],
float]`, default `time.monotonic`) and an **injected `random.Random`**, so every acceptance target
is locally reproducible under `asyncio.run` without a cloud fleet.

| # | Mechanism | Where |
|---|---|---|
| 1 | `OVERLOAD_SHED` posture code (codes-only, shared vocabulary); reuses `MIN_RETRY_AFTER_S` | `gateway_v2/domain/posture.py` |
| 2 | Pure per-owner CoDel state machine — target 5 ms / interval 100 ms / hard cap 60 ms, `sqrt(count)` backoff, quiescence reset | `gateway_v2/admit/codel.py` |
| 3 | `ShedVerdict` + `Admitted` + jittered Retry-After helper (`shed_retry_after_s`, ≥ 1 s, `should_retry=False`) | `gateway_v2/admit/grant.py` |
| 4 | Bounds derivation — `derive_bounds` from `ResourceContract` + injected `q_safe`; refuse-to-start if unset; no capacity literal | `gateway_v2/admit/quota.py` |
| 5 | Producer-only, **label-free** metrics — per-queue depth/age, admitted/shed totals, `fail_open_total` pinned 0 | `gateway_v2/admit/metrics.py` |
| 6 | `BoundedQueue` + `BackpressureBuffer` — declared max depth, shed-at-the-door, oldest-item age | `gateway_v2/admit/queues.py` |
| 7 | `WorkerSupervisor` seam — `isolate` + injected respawn; real OS supervision = serving-entrypoint card | `gateway_v2/admit/supervisor.py` |
| 8 | `AdmissionController` — per-owner registry, admit path, drain state machine, backpressure | `gateway_v2/admit/admission.py` |
| 9 | Public surface — `AdmissionController`, `AdmissionBounds`, `derive_bounds`, `ShedVerdict`, `Admitted`, `CoDelParams`, `DrainState`, `DrainReport`, `AdmissionMetrics` | `gateway_v2/admit/__init__.py` |

---

## 2. What was built

### 2.1 The posture code (`domain/posture.py`, codes-only)

`OVERLOAD_SHED = "overload_shed"` is added to the one shared posture vocabulary as a **code only** —
no status, no message, no `ErrorSpec` — exactly like the existing `KILL_SWITCH_UNAVAILABLE` /
`SHARED_STATE_UNAVAILABLE` / `PLAN_UNAVAILABLE` / `BUDGET_UNAVAILABLE` codes. `edge` owns the HTTP
mapping. `MIN_RETRY_AFTER_S = 1.0` already existed with its R2-08 docstring and is **reused** as the
Retry-After floor, not redefined in `admit`.

### 2.2 The CoDel state machine (`admit/codel.py`, pure)

`CoDelController` is a pure per-owner state machine — no event loop, no I/O, a fold over
`(now, sojourn)` observations against the injected clock, so it is property-testable in isolation.
`CoDelParams(target_ms=5.0, interval_ms=100.0, hard_cap_ms=60.0)` carries the owner-signed values.
`observe_and_decide(sojourn_ms, now)` implements the exact algorithm: a **hard-cap short-circuit**
(sojourn ≥ 60 ms → `SHED_HARD_CAP` regardless of backoff state); `sojourn <= target` clears the
excursion and admits (**quiescence / reset**); the first sample above target starts the excursion;
shedding fires only once the minimum sojourn has stayed above target for a full `Interval`; and the
backoff schedule contracts as `drop_next_at = now + interval_ms / sqrt(count)` so shedding grows
more aggressive the longer overload persists. `reset()` restores quiescence. Admission depends on
**sojourn + CoDel state only, never prompt size** — the 12 ms instantaneous bound does not exist.

### 2.3 The shed verdict + jitter (`admit/grant.py`)

Frozen slotted `ShedVerdict(code, retry_after_s, should_retry, request_id)` with
`code = posture.OVERLOAD_SHED` and `should_retry` **always `False`** on a shed. Frozen slotted
`Admitted(owner_id, request_id, admitted_at)` marks a request that passed the door.
`shed_retry_after_s(rng, floor_s=MIN_RETRY_AFTER_S, jitter_frac=0.5)` returns
`floor_s + rng.random() * jitter_frac * floor_s` — always ≥ 1.0 s, so it can **never** land in the
6–11 ms band, and deterministic given the injected `rng`. The verdict is a **value, not an HTTP
object**: `edge` renders `503` + `Retry-After: ceil(retry_after_s)` + `x-should-retry: false` and
echoes `request_id`.

### 2.4 The bounds derivation (`admit/quota.py`)

Frozen slotted `AdmissionBounds(concurrency, request_depth, guard_depth, dispatch_depth,
egress_depth, audit_depth)`. `derive_bounds(contract, q_safe, audit_drain_rate_per_s,
audit_bytes_per_record)` **refuses to start** (`CapacityUnset`) if `q_safe` is `None` or ≤ 0, then
derives concurrency / request / guard / dispatch from `contract.queue_depth(q_safe)`, the egress
slot bound from `queue_depth(q_safe)` plus the byte bound from `contract.stream_buffer_bytes(…)`,
and the audit depth from `contract.audit_queue_depth(audit_drain_rate_per_s,
audit_bytes_per_record)` (deliberately **not** `queue_depth`, per the contract docstring). **No
numeric capacity literal lives in `admit`** — every number is a `ResourceContract` call; an
uncomputable bound propagates `CapacityUnavailable`. An AST gate enforces the no-literal discipline.

### 2.5 The metrics (`admit/metrics.py`, producer-only)

Follows the gateway_v2 producer-vs-publisher split (`runtime/holdback_metrics.py`): a fixed,
**label-free**, no-tenant-label series set — GW14d owns fleet publication. Per-queue depth and
oldest-item age (the queue name is a fixed finite set, not tenant-derived), `admitted_total`,
`shed_total{reason}` over a fixed reason set, and `fail_open_total` **pinned at `0` from the first
snapshot**. Observe is O(1); percentiles are computed on read; counters are **real zeros, never
absence**. Frozen slotted `QueueReport(queue_name -> (depth, oldest_age_s))`.

### 2.6 The bounded queues (`admit/queues.py`)

`BoundedQueue` carries a declared max depth from `AdmissionBounds`; the enqueue rule is
`if depth >= max: shed at the door` (never exceed the bound), and it tracks the current depth and
the oldest-item age against the injected clock, feeding `AdmissionMetrics` / `QueueReport`.
`BackpressureBuffer` caps a slow consumer's buffered bytes by the egress byte bound
(`stream_buffer_bytes`) and stops feeding a consumer once its credit is exhausted, so total
streaming memory stays bounded across any number of slow consumers while fast consumers draw from
their own credit.

### 2.7 The supervisor seam (`admit/supervisor.py`)

`WorkerSupervisor` is a small injectable abstraction: `on_worker_exit(worker_id) -> respawn()` and
`isolate(worker_id)` remove a crashed worker's slots from the shared accounting **without touching
peers**. The real process `fork`/respawn is injected (`Callable[[], Worker]`) and defaults to a stub
that records intent — **real OS supervision is the serving-entrypoint card's job (declared
dependency, Req 3.4 / 9).** Locally it is driven with a fake worker that crashes by raising; the
test asserts isolation, respawn-called, and that peers keep admitting.

### 2.8 The controller (`admit/admission.py`, event-loop seam)

`AdmissionController(bounds, params, clock=time.monotonic, rng, supervisor, metrics,
drain_window_s)` is the single module that touches the event loop. `async admit(owner_id,
request_id, enqueued_at)` computes `sojourn = now - enqueued_at` (ms), consults the owner's
`CoDelController` from a **per-owner registry** (lazily created from one factory — the guard owner
gets a controller from the **same** factory, no special global path), and either enqueues
(**admitted — never dropped thereafter**) or returns a `ShedVerdict`. Enqueue is gated by the
bounded queue depth (shed at the door when full). Any undecidable path **sheds (fail closed)**;
`shed_total` increments; `fail_open_total` stays `0`. `queue_report()` exposes depth + oldest-age.

**Fairness** (Property 3) is structural: owner A's `first_above_at` / `dropping` / `count` live only
in A's controller, so A saturating cannot advance B's `drop_next_at` or raise B's shed count. The
guard queue is itself one of the bounded queues and is admission-bounded like every other owner.

**Drain** (Req 8). `DrainState(StrEnum)` = `ACCEPTING`, `DRAINING`, `TERMINATING`, `FLUSHED`,
`EXITED`. `async drain()` stops accepting on SIGTERM (`ACCEPTING → DRAINING`), finishes in-flight
within `Drain_Window`, past the window sends a `Declared_Termination` terminal frame per unfinished
stream (**never truncated**), flushes the audit queue before exit, and publishes the measured
`DrainReport(duration_s, declared_terminations, audit_flushed)`. All timing reads the injected clock,
so the state machine is deterministic.

### 2.9 The public surface (`admit/__init__.py`)

Re-exports `AdmissionController`, `AdmissionBounds`, `derive_bounds`, `ShedVerdict`, `Admitted`,
`CoDelParams`, `DrainState`, `DrainReport`, `AdmissionMetrics` so `edge` consumes a stable surface.
Nothing imports a layer above `admit`.

### The owner-signed CoDel parameters and the per-owner fairness model

| Parameter | Value | Role |
|---|---|---|
| `Target` | 5 ms | sojourn target below which CoDel never sheds (quiescence) |
| `Interval` | 100 ms | sliding window over which the minimum sojourn is tracked |
| `Hard_Cap` | 60 ms | absolute ceiling; a sojourn at the cap sheds regardless of backoff state |
| backoff | `interval / sqrt(count)` | the inter-drop gap contracts under sustained overload |

Admission state is keyed strictly per `owner_id`; there is no shared `first_above_at` / `dropping` /
`count`. A saturating owner's observations mutate only its own controller and consume only its own
share of the per-owner queue capacity — so one owner's arrival pattern cannot raise another owner's
shed rate above that owner's fair share. Fairness is an **invariant of the keying**, not an emergent
property of a global FIFO (which is exactly what R2-07 removes).

### The codes-vs-render boundary and the layer rule

`admit` **never constructs an HTTP object**: `admit` returns a frozen `ShedVerdict` (a code +
`retry_after_s` + `should_retry` + `request_id`) and `edge` renders the 503. This is the same
codes-vs-render split `posture.py` already enforces for `SHARED_STATE_UNAVAILABLE` etc. `admit`
imports only `runtime` (`ResourceContract`, `CapacityUnavailable`, `CapacityUnset`) and `domain`
(`posture.*`) — both below it — and **never** `edge`, `plan`, `detect`, `resolve`, `dispatch`,
`egress`, or `audit`. The admission-side backpressure cross-references `egress/backpressure.py` /
`egress/stream.py` as the eventual real transport but does **not** import `egress` (layer rule). The
import-linter `layers` and `forbidden` contracts verify this.

---

## 3. Verification

### 3.1 Gates (all green, local scope)

| Gate | Result |
|---|---|
| `pytest tests/` (full offline suite) | **1057 passed**, 93 skipped, 1 xfailed, 0 failed |
| `pytest -k lgw19` (this card's subset) | **99 passed**, 1052 deselected |
| `pytest tests/admit/` (the admit package) | **163 passed** |
| `mypy --strict gateway_v2` | clean, 107 source files |
| `ruff check gateway_v2` | clean |
| `import-linter` | **2 contracts kept, 0 broken** (`Gateway v2 layers`, `Resolve imports only domain`); 107 files, 150 dependencies analyzed |
| AST capacity-literal gate (`tests/gates/test_lgw19_admit_capacity_literals.py`) | **3 passed** — no numeric capacity literal in `admit/` except via a `ResourceContract` call |

The 10 correctness properties are each exercised as a seeded `random.Random` loop of **≥ 10,000
iterations** (the house idiom from `tests/domain/test_lgw04.py`; **no `hypothesis` dependency is
added**), tagged `# Feature: admission-control, Property N`.

### 3.2 LGW19-1 — 3× q_safe overload burst

Offered load at **3× the injected `q_safe`** drives explicit shedding: **≈66.7 % shed** (two of
every three offered requests), admitted **p99 = 1.0 ms** well inside the 20 ms SLO budget, the
**peak request-queue depth equals the declared cap** (bounded memory — the queue, not an unbounded
float, bounds the sojourn), and **0 FAIL_OPEN** (`fail_open_total` stayed `0`). Mirrors the §0.2
live shape (admitted p99 ≈ 17.8 ms under a 4.3× burst, 0 FAIL_OPEN) at scaled-down, deterministic
in-process scale. Validates Req 12.2 / Properties 2, 4.

### 3.3 LGW19-4 — the concurrency cap binds and is observable

The derived concurrency cap **binds** and is observable through `queue_report()` / the metrics
series — not inert as the v1 `--worker-connections` flag was. Validates Req 12.6.

### 3.4 LGW19-2 — open-loop arrival-rate ramp

Ramping the offered arrival rate open-loop until admitted p99 crossed the budget recorded the
**highest-passing rate = 2000 req/s** and the **first-failing rate = 2500 req/s** (first-fail
strictly above highest-pass on the monotone ramp). Validates Req 12.3.

### 3.5 LGW19-5 — guard rate halved mid-run

Halving the injected guard service rate mid-run drove the shed rate up from **16.6 % → 58.2 %** while
the **queue age stayed bounded** — the controller responds to the capacity drop by shedding rather
than by letting the backlog (and sojourn) grow unbounded. Validates Req 12.4.

### 3.6 LGW19-6 — slow-consumer backpressure

Slow consumers on **50 % of streams** confirmed backpressure engages, total buffered memory stayed
within the declared byte bound, and the fast consumers were **unaffected** by the slow ones.
Validates Req 12.5 / Property 4.

### 3.7 G-06 local — SDK-retry-amplification

Under the local SDK-retry-semantics simulation (`_SDK_MAX_RETRIES = 2`), this component's shed
policy (`should_retry=False`, Retry-After ≥ 1 s) produced an amplification factor of **1.0×** —
strictly below the **1.5× G-06 bound** — while the replaced **6–11 ms baseline amplified ≥ 2.6×
(≈3.0×)** because the SDK retried each shed twice. Validates Req 6.5 / Property 8. (The live G-06
against the real OpenAI SDK is deferred.)

### 3.8 G-15 local — injected-clock post-heal recovery

After an injected store heal (pre-heal backlog built to the hard cap), the guard drained the backlog
and CoDel reset on the first sub-Target sojourn, so admitted latency returned to within the SLO in
**≈0.055 s** — far inside the **10 s recovery budget** — with `fail_open_total` still `0`. Validates
Req 11.1 / Property 9. (The live G-15 fleet certification is deferred.)

### 3.9 The 10 correctness properties

| # | Property | Validates |
|---|---|---|
| 1 | No abandonment | 2.1, 2.2, 2.3 |
| 2 | Shedding under sustained overload with bounded admitted p99 | 1.3, 5.4, 12.2 |
| 3 | Per-tenant fairness | 1.1, 1.7 |
| 4 | Queue-depth bound | 7.1, 7.2, 10.1, 10.2 |
| 5 | Retry-After floor and should-retry | 5.2, 5.3, 6.1, 6.2, 6.3, 6.4 |
| 6 | CoDel quiescence | 1.2 |
| 7 | Drain terminal guarantee | 8.2, 8.3 |
| 8 | Retry-amplification bound | 6.5 |
| 9 | Post-heal recovery bound | 11.1 |
| 10 | Error conditions fail closed | 4.5, 13.1, 13.2, 13.3 |

Each runs as a seeded `random.Random` loop of ≥ 10,000 iterations.

---

## 4. Open, and honestly partial

### 4.1 Deferred cloud / scale gates (out of local scope — no cloud resources)

| Item | Why it is not closed here | Requirement |
|---|---|---|
| **Full live fleet 3×-`q_safe` run** (LGW19-1 at real scale) | A **deferred cloud gate**: needs the fleet lane and a serving gateway; the serving entrypoint in the rewrite tree is a stub. Represented locally by §3.2. | 12.7 |
| **GW20 live `q_safe` measurement** feeding this card | `q_safe` is **injected**; the measured guard rate is produced by card GW20 and fed back. The live measurement is deferred; locally `q_safe` is injected per step. | 12.7 |
| **Live G-06** against the real OpenAI SDK | A **deferred cloud gate**; represented locally by the in-process SDK-retry-semantics simulation (§3.7). | 12.7 |
| **Live G-15** post-heal certification | A **deferred cloud gate**; represented locally by the injected-clock recovery test (§3.8). | 11.2 |

### 4.2 The serving-entrypoint-card dependency

Three items belong to the **serving-entrypoint card**, not this component (Req 3.4 / 9):

- removing the inert `--worker-connections` flag;
- migrating off the deprecated uvicorn worker class;
- **real OS process supervision** (`fork`/respawn).

This card ships the `WorkerSupervisor` **seam** (an injectable respawn callable + `isolate`) and the
**observable** concurrency cap only; the real supervision drops in behind the seam.

### 4.3 Found, not fixed / honest notes

- **A ms/seconds unit bug the fairness property caught.** During the build the per-owner fairness
  property surfaced a milliseconds-vs-seconds mix-up in the sojourn accounting; it is **now fixed**,
  and Property 3 passes across ≥ 10,000 iterations.
- **The harness is an in-process injected-clock simulation**, not a wall-clock fleet. Every number
  in §3 (admitted p99, highest-pass / first-fail, shed rates, amplification, recovery window) is
  computed from the simulated sojourn samples under the fake clock, so the numbers are
  **deterministic** and not sensitive to CI wall-clock jitter — the same realism caveat the R2-06
  holdback benchmark carries.
- **Post-heal recovery is cross-component.** This card **owns** the admission backlog drain and the
  bounded audit queue. The **lease-refill storm** belongs to GW06 (budget/lease — note
  `posture.BUDGET_UNAVAILABLE` is reserved for it) and the **cold identity caches / store
  re-hydration** belong to GW05 (`IdentityCache` single-flight + re-hydrator). The local recovery
  test drives only the admission-owned portion.
- **The egress transport / backpressure wiring is GW13.** Admission owns only the **admission-side**
  slot / credit bound; the real SSE transport and credit wiring are `egress/backpressure.py` /
  `egress/stream.py`, which admission cross-references but does not import (layer rule).

---

## 5. Files

**New.** `gateway_v2/admit/codel.py`, `gateway_v2/admit/metrics.py`, `gateway_v2/admit/queues.py`,
`gateway_v2/admit/supervisor.py`, `gateway_v2/admit/admission.py`, and the test modules under
`gateway_v2/tests/admit/test_lgw19_*.py` (codel, grant, quota, metrics, queues, admission, fairness,
drain, supervisor, backpressure, harness, edge_seam) plus the AST gate
`gateway_v2/tests/gates/test_lgw19_admit_capacity_literals.py`.

**Modified (codes-only / filled / surface).** `gateway_v2/domain/posture.py` (`OVERLOAD_SHED` code),
`gateway_v2/admit/grant.py` (`ShedVerdict` + jitter), `gateway_v2/admit/quota.py` (`derive_bounds`),
`gateway_v2/admit/__init__.py` (public surface).
