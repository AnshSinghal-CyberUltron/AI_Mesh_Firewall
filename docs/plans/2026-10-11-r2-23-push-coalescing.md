# R2-23 — Per-owner push-nudge coalescing (C43 push cost)

**Status:** implemented, local half CLOSED. **Register item:** R2-23 (C43 push cost). **Phase:** Observability / control-plane.
**Depends:** GW05c (per-record publish). **Fix before:** GW19. **Impact:** MEDIUM.
**Date:** 2026-10-11. **Spec:** `.kiro/specs/push-coalescing/`.
**Evidence:** `docs/plans/evidence/2026-10-11-r2-23-push-coalescing/`.

---

## 1. Executive summary

R2-23 records "C43 push cost: 72 pushes per request at 36 workers, +2.3 gateway CPU-ms per
request. Push coalescing was deferred from RC2; it exists only in the unreleased RC3 base and was
never measured." The correction: "GW08 / GW19 bound push rate per owner (coalesced), with a
microbenchmark and a targeted knee re-measure."

**The honest framing — most of this defect is already closed by GW05c, and this card says so.** The
original "72 pushes per request" was a v1/RC2 property: the control plane PUBLISHed a nudge per
worker per request on the serving path. In the v3 tree (`gateway_v2/`) that is GONE by construction:

- The **write side** (`state_control/valkey.py` + `publisher.py`) emits exactly ONE `PUBLISH` per
  record write and ONE manifest-last `PUBLISH` per batch — never per worker, never per request.
- The **read side** (`runtime/state_nudge.py` `NudgeListener`) already coalesces: `drain_max=64`
  messages per wakeup collapse into one nudge, and a worker whose cursor is at/past the announced
  `<kind>:<feed_seq>` skips the round entirely, so "a nudge storm costs nothing".
- There is **no serving-path publish at all** in `gateway_v2`.

So R2-23's per-request/per-worker defect is a VERIFICATION OBLIGATION here (a standing regression
tripwire), not a re-fix. What genuinely remained, and what this card builds, is the piece the
correction names explicitly: a **per-owner, publish-side nudge coalescer** that bounds how many
`PUBLISH`es one owner's *write burst* drives — today a burst of writes to a single owner still emits
one `PUBLISH` each — plus the **microbenchmark** the register asks for. The fleet-scale "targeted
knee re-measure" is the one DEFERRED CLOUD GATE, declared OPEN.

The coalescer is a thin decorator around the `StatePublisher` protocol: it always delegates the
Durable_Write to the inner publisher, and coalesces ONLY the lossy nudge. Coalescing a lossy hint is
always safe because the periodic O(1) round is the correctness backstop — a merged or dropped nudge
costs only latency, bounded by the Freshness_Window.

| # | Mechanism | Where |
|---|---|---|
| 1 | `StateKnobs` gains `coalesce_window_ms` / `per_owner_push_bound` (named-constant defaults) + a fail-at-start `window <= enforcement_bound_s` (Freshness_Window) check | `gateway_v2/domain/state_knobs.py` |
| 2 | `StatePublisher` protocol gains `emit_nudge(kind, feed_seq)` + a `nudge: bool = True` keyword so the Durable_Write can skip its inline `PUBLISH`; `MemoryStore` + `BrokenPublisher` implement them | `gateway_v2/state_control/publisher.py` |
| 3 | `ValkeyPublisher._guarded` makes the inline `PUBLISH` conditional on `nudge` (durable MULTI byte-identical); `emit_nudge` is a bare `PUBLISH`, payload shared via `_nudge_payload` | `gateway_v2/state_control/valkey.py` |
| 4 | `CoalescingPublisher` — the per-owner decorator: `CoalescerCounters` (label-free), `CoalesceConfig.from_knobs`, `_PendingNudge` per-(owner,kind) accounting, durability-first coalesce, monotonic guard, three-layer flush | `gateway_v2/state_control/coalescer.py` |
| 5 | Service build wraps the WRITER's publisher in the coalescer, keeps the re-hydrator on the bare publisher, calls `flush_due` each round + `flush_all` on shutdown | `gateway_v2/state_control/service.py` |

---

## 2. What was built (bottom-up)

### 2.1 The authority (`domain/state_knobs.py`)

Two new knobs with named-constant defaults (`DEFAULT_COALESCE_WINDOW_MS = 250`,
`DEFAULT_PER_OWNER_PUSH_BOUND = 1`) and `__post_init__` fail-at-start validation: non-negative
window, bound >= 1, and `coalesce_window_ms <= enforcement_bound_s * 1000` (the window must not
outlive the Freshness_Window backstop; the `==` boundary is accepted). The defaults are named
constants, not bare literals at the field, so the capacity-literals AST gate stays green.
`enforcement_bound_s` (rehydrate + refresh period) already existed and IS the Freshness_Window.

### 2.2 Separating the nudge from the durable write (`publisher.py` + `valkey.py`)

The core design problem: the inner publisher does the Durable_Write AND the `PUBLISH` in one
`_guarded` MULTI. To coalesce ONLY the nudge, the `StatePublisher` protocol gains `emit_nudge` and a
`nudge: bool = True` keyword. `ValkeyPublisher._guarded` makes only the trailing `pipe.publish(...)`
conditional on `nudge` — the durable `SET`/`HSET` + `ZADD` + `SADD`/`SREM` + manifest `SET` staging
stays **byte-identical** and manifest-last. `emit_nudge` is a standalone bare `PUBLISH`; a shared
`_nudge_payload` helper keeps the inline nudge and `emit_nudge` byte-identical so `parse_nudge`
round-trips both. `MemoryStore` gates its `.nudges` append on the flag (durable state + `record_writes`
identical regardless). The `nudge=True` default keeps every existing caller unchanged.

### 2.3 The coalescer (`state_control/coalescer.py`)

`CoalescingPublisher(inner, config, clock, metrics)` implements the whole `StatePublisher` protocol.
Reads and `publish_kind` (the re-hydrator's wholesale restore) pass through uncoalesced.
`publish_record` / `publish_batch` are durability-first: they delegate to `inner.publish_*(...,
nudge=False)` and **capture the bool before any coalescing**, so the writer's outcome mapping is the
inner result unchanged; then they run the coalesce algorithm inside a swallow-everything `try/except`,
so a push failure can never alter a `WriteOutcome`. The algorithm (per design §4), keyed by
`(owner, kind)` where owner = `record.key`: roll the window when it has elapsed (flushing the
previous window's residue at Highest_Feed_Seq), fold the new `feed_seq` into `highest_pending`, and
emit while under `per_owner_push_bound` — else hold. `_emit` applies the monotonic guard (a position
`<= highest_announced` is suppressed and counted as `pushes_saved`, never put on the wire).
`flush_due(now)` (window elapsed + residue) and `flush_all()` (shutdown, regardless of window)
complete the three-layer flush; both are fail-safe (per-entry swallow) and idempotent on a quiesced
owner. `CoalescerCounters` is a frozen label-free two-series producer mirroring `ListenerCounters`.

### 2.4 Service wiring (`service.py`)

`build()` wraps the WRITER's publisher in `CoalescingPublisher(from_knobs(config.knobs),
clock=time.monotonic)` and keeps the re-hydrator's `Rehydrator` on the BARE publisher (its
wholesale `publish_kind` restore must not be coalesced — R2-04 HA). The loop calls `flush_due(now)`
each round (so a quiesced owner's held nudge goes out within one re-hydrator period, well inside the
Freshness_Window) and `flush_all()` on shutdown, defensively guarded. `StateWriter` is untouched —
it depends only on the `StatePublisher` protocol.

### 2.5 Why coalescing a lossy hint is always safe

Durability is never coalesced (the durable write is delegated with `nudge=False` and returns before
any coalescing). The nudge is a latency hint; the periodic O(1) round is the correctness backstop. A
merged or dropped nudge only means a worker learns via the next round, bounded by the
Freshness_Window. The coalescer re-validates that its window never exceeds the Freshness_Window at
construction, so even the worst case (a held nudge never flushed) leaves no worker behind beyond the
backstop.

---

## 3. Verification

| Gate | Result |
|---|---|
| R2-23 new tests (10 property + 6 verification + 7 microbench) | **23 passed** |
| `pytest tests/state_control` (full suite, regression) | **188 passed**, 58 skipped, 1 xfailed |
| `mypy --strict` (full tree) | clean, **153 source files** |
| `ruff check` (incl. tests) | clean |
| `import-linter` | **2 contracts kept, 0 broken** (state_control's `gateway_v2.domain.state_knobs` import is the allowed `state_control -> gateway_v2.domain` direction) |
| capacity-literals AST gate over `gateway_v2` | PASS (the StateKnobs defaults are named constants) |

The **10 correctness properties** each run as a seeded `random.Random` loop of >= 10,000 iterations
(house idiom; no `hypothesis`), one property per test (seeds 0xc0a01–0xc0a10). The **microbenchmark**
(the local representative of the deferred knee re-measure) measured **46 pushes saved** on a seeded
~72-write burst workload (72 un-coalesced -> 26 coalesced) with Durable_Write parity (72 == 72).

Two **test-harness** bugs surfaced during the property sweep (counting the window-close catch-up
push as an in-window push) and were fixed in the tests — the shipped coalescer matches design §4
exactly; no product change was made and no property was weakened.

---

## 4. Deferred cloud gate (out of local scope)

- **Knee_Re_Measure (Req 9)** — the register's "targeted knee re-measure" at fleet scale (36 workers
  / real Valkey / real request load) of push cost at the capacity knee. Declared **OPEN**,
  out-of-local-scope. The Microbenchmark is its LOCAL REPRESENTATIVE and does NOT satisfy, pass, or
  close it; a test guards that the harness never marks it closed.

---

## 5. Honest notes

- The per-request / per-worker "72 pushes per request" defect is **already closed by GW05c** — this
  card VERIFIES that (Requirement 1 is a standing regression tripwire, encoded as an AST guard that
  no serving package reaches the write-side publish surface), it does not re-fix it.
- The genuinely-remaining piece is the per-owner publish-nudge coalescer + the microbenchmark —
  category-1 clean: the coalescer is production code that runs identically local and cloud; only the
  fleet knee-remeasure is deferred.
- Durability is NEVER coalesced — the durable MULTI is byte-identical with the `PUBLISH` moved out,
  and the coalesce path is swallow-guarded after the durable write returns, so a push failure can
  never change a `WriteOutcome`.
- The pre-existing documented R2-05/GW05c `check_tenant_scale state_control` finding at
  `state_control/valkey.py:100` is unrelated and untouched.

---

## 6. Commits (per-layer, `suraj-revamp`)

```
a3ea0dfb feat(r2-23): StateKnobs coalesce-window + per-owner-push-bound authority
6d59f8b7 feat(r2-23): separate the nudge from the durable write on StatePublisher
18f76bb1 feat(r2-23): make the inline PUBLISH conditional in ValkeyPublisher
da03c2d4 feat(r2-23): the per-owner nudge coalescer (state_control/coalescer.py)
36ded32c feat(r2-23): wire the coalescer into the re-hydrator service
bfb0ebe0 test(r2-23): 10 property suites + verification obligation + microbenchmark
```
