# R2-04 evidence — concurrent-publish safety, the prod overlay, and the lock-free head read

Date: 2026-10-08. Card GW05b, runbook row R2-04. Third and final local instalment, after
`bounds-reach-the-server.md` and `process-and-deployment.md`.

## 1. "Proven safe concurrently" — the clause the HA work itself endangered

R2-04 requires *">= 2 re-hydrators ... (proven safe concurrently)"*. `ValkeyPublisher.publish_kind`
called `_guarded(..., guard=False)` for **all nine** repair reasons, disabling the check that
otherwise refuses to move a kind's manifest backwards.

`guard=False` is genuinely **necessary** for one reason only — `store_ahead`, where the store holds
a `feed_seq` Postgres never issued. `repair` bumps the epoch first, but `_is_ahead` compares
`feed_seq`, which an epoch bump does not lift above the store's, so a guarded publish would be
refused and the divergence would never heal.

Applied to the other eight reasons it is a regress window, and it was reachable only once two
re-hydrators actually ran — which the previous instalment delivered:

```
store flushed; A and B both diagnose MISSING
A: snapshot(plan) -> feed_seq N
                              a writer commits N+1 and publishes it
B: snapshot -> N+1 -> publish_kind(N+1)        manifest = N+1
A: publish_kind(N)  guard=False                manifest = N     <- REGRESS
readers refuse a regress (C36); the kind is unreadable until the next round
```

**Fix.** `publish_kind(..., *, allow_regress: bool = False)` on the `StatePublisher` Protocol,
`ValkeyPublisher`, `MemoryStore` and `BrokenPublisher`; `ValkeyPublisher` passes
`guard=not allow_regress`; `Rehydrator.repair` passes `allow_regress=(reason == STORE_AHEAD)`.
Keyword-only and defaulting to `False`, so a new caller is safe by default and cannot regress a
kind positionally by accident.

`MemoryStore.publish_kind` had **no** `_is_ahead` check at all, so the twin was more permissive
than the product and a regress bug could not reproduce in the offline suite — the suite CI runs.
It now refuses by default too, counted via `whole_kind_publishes_refused`.

**Tests.** `test_a_slower_rehydrators_stale_repair_cannot_regress_a_kind` (the regress is refused
and the manifest does not move), `test_the_store_ahead_repair_may_still_regress_because_it_has_to`
(the exemption still works), `test_the_twin_and_the_product_agree_on_the_guard` (signature parity
across all three implementations, keyword-only, default `False`).

**Five existing fixtures started failing, which is the proof the guard bites.** Each was using
`publish_kind` to force the store *backwards* on purpose — simulating a rollback or a stray backup
restore. They now declare `allow_regress=True`, so a deliberate regress is visible at the call
site instead of being the silent default:

- `tests/runtime/test_lgw05b_stamp_view.py` ×2
- `tests/state_control/test_lgw05b_pg_grace.py` ×1 (+ a stub signature)
- `tests/state_control/test_lgw05b_rehydrate_stamp.py` ×1 stub signature
- `tests/state_control/test_lgw05c_drills.py` ×1 and `test_lgw05c_live_stack.py` ×2 (live-only)

## 2. A pre-existing defect found while testing this — NOT R2-04

Writing `test_the_store_ahead_repair_may_still_regress_because_it_has_to` surfaced an unrelated
bug. After a `store_ahead` repair the kind **never settles**:

```
forced: store_ahead
round 1: repairs=['store_ahead'] diagnose='index' whole_kind_publishes=1
round 2: repairs=['index']       diagnose='index' whole_kind_publishes=2
round 3: repairs=['index']       diagnose='index' whole_kind_publishes=3
...
round 8: repairs=['index']       diagnose='index' whole_kind_publishes=8
```

**Root cause.** `repair_epoch` advances the counter row to `feed_seq = N+1`, but the records keep
their original `feed_seq <= N` (the snapshot returns them unchanged). So `manifest.feed_seq = N+1`
while `head.index_top = N`, and `_compare` hits
`head.index_top < manifest.feed_seq` -> `INDEX` on every subsequent round. The result is a
permanent **O(records) whole-kind republish once per period**, on a path GW05c explicitly requires
to be O(1) ("per-record publish, never a whole-kind rewrite"). At 25k tenants that is RC2's
17.4 MB / 124 ms publish, every second, for ever.

This is a **GW05c / R2-02 defect, not R2-04's**, and it predates every change in this series — my
work only made it visible, because nothing previously asserted that a `store_ahead` divergence
heals. Fixing it means either re-signing the records at new positions during an epoch repair or
comparing `index_top` against the records' own max `feed_seq`; both are GW05c design decisions and
not R2-04's to guess at.

Recorded in code rather than only in prose, as
`test_a_store_ahead_repair_settles_instead_of_looping` with `@pytest.mark.xfail(strict=True)`. It
shows as `xfailed` today and will **fail the suite** the moment it starts passing, forcing whoever
fixes it to remove the marker.

## 3. Prod ran zero re-hydrators — and would have run the wrong image

Two separate problems, both found by checking rather than assuming.

**(a) Absent from the overlay.** `docker-compose.prod.yml`'s header asserts *"This overlay covers
EVERY service defined in docker-compose.yml"* and enumerates them. The two re-hydrators were not
there, so a prod deployment would have run none — no freshness stamp, and the whole fleet failing
closed at `AMF_STATE_FRESH_MS` once the v3 gateway serves traffic. Both are now always-on
(`profiles: !reset null`), 192 MiB each, `restart: unless-stopped`, and named in the header.

**(b) The wrong image.** The obvious wiring — `*ecr_gateway_image` — would have been silently
broken: `ai-mesh-gateway` is built from `gateway/Dockerfile`, the **v1** gateway, which contains no
`state_control` package. Two containers that cannot start, with a symptom (no stamp, fleet fails
closed) that looks nothing like the cause. Added a dedicated `ai-mesh-state-control` image built
from `gateway_v2/Dockerfile`, wired through `infra/scripts/build-push-images.sh` (both repo loops,
a build block, and the summary list) and a new `x-ecr-state-control` anchor.

Verified on the merged configuration:

```
state-rehydrator-a     .../ai-mesh-state-control:latest
state-rehydrator-b     .../ai-mesh-state-control:latest
gateway                .../ai-mesh-gateway:latest
```

both with `command: ['python','-m','state_control']`, `restart: unless-stopped`, 192 MiB, distinct
`AMF_REHYDRATOR_ID` / `AMF_DEPLOY_ZONE`, the idle-stop exemption and a healthcheck.

`tests/gates/test_r2_04_deployment.py` grew to 16 tests covering the prod overlay, the header
claim, the dedicated image, and that the image is actually built and pushed.

## 4. The per-round head read is now lock-free (G13)

`PostgresControlDB.counters` went through `tx()` — a read-write `READ COMMITTED` transaction whose
first statement is `INSERT ... ON CONFLICT DO NOTHING` — four times a second per re-hydrator, on
the path the fleet's availability depends on. It avoided blocking on a held `FOR UPDATE` only
because an `ON CONFLICT` probe does not wait on a lock-only `xmax`; relying on that subtlety rather
than on taking no lock at all is the wrong way round.

It now uses `_connect(read_only=True)` with a bare `SELECT`, returning `ZERO_COUNTERS` for an
absent row exactly as `snapshot` does. Row creation stays in `PostgresTx.counters`, used by the
writer — the only caller that needs the row to exist.

Not a runbook clause (the runbook requires lock-freedom of *"the publish snapshot"*, which was
already correct), but it removes the last way a lock can touch the loop.

## 5. The drill my own work made ambiguous

`test_drill_rehydration_under_a_held_row_lock` held its lock through `PostgresControlDB.tx()`.
Once the session bounds actually reached the server, that holder became subject to
`idle_in_transaction_session_timeout` (5 s) — against an assertion of `took_s < 5.0`. It would have
passed whether the repair completed under the lock or merely outlived the holder, which is the one
distinction the drill exists to prove.

Now holds the lock from a raw `psycopg.connect(DSN)` carrying none of our options, asserts the
holder was **still open** when the repair finished, and tightens the bound to `< 2.0 s` so the
result cannot be explained by the holder's session being killed.

## 6. Gates

| Gate | Result |
|---|---|
| offline suite (the CI shape) | **607 passed, 62 skipped, 1 xfailed** |
| live suite (real Postgres + real Valkey) | **665 passed, 4 skipped, 1 xfailed** |
| both lock drills | passed; `:453` now tightened |
| 5 AST gates × 2 trees, tenant-scale | clean |
| import-linter | 2 kept, 0 broken |
| ruff / `mypy --strict` | clean, 114 files |
| `docker compose config` base and base+prod | both parse; correct image resolved |
| `bash -n build-push-images.sh` | clean |

Final live run, 60 plans + key + kill-switch + budget, two re-hydrators:

```
amf_rehydration_attempts_total 5
amf_rehydration_success_total 5
amf_rehydration_failure_total 0
amf_rehydration_repairs_total{kind="plan"} 0
amf_state_stamp_written_total 5
amf_state_publish_pending_oldest_seconds 0.0
amf_rehydrator_active 1
republishing (whole-kind) occurrences in the log: 0
```

Zero repairs and zero whole-kind publishes in the steady state, which is the O(1) round behaving.

## 7. R2-04 from our side

| Clause | State |
|---|---|
| four timeouts on every control-plane connection | **done**, verified against a real server and through a real PgBouncer |
| lock-free `REPEATABLE READ` publish snapshot | **done** (GW05c), re-verified; head read now lock-free too |
| a fired bound is a bounded fault, not an outage | **done** |
| >= 2 re-hydrators | **done** — base + prod, dedicated image, both always-on |
| ...proven safe concurrently | **done** — regress guard scoped, twin parity, tests |
| never lets an idle-stop policy touch them | **done** — identity-based exemption, gated, negative control verified |
| alarm on `ok_publish_pending` older than one period | **done** — producer added, no schema change |
| >= 2 **zones** | **NOT closable locally** — declared as labels; needs two single-zone MIGs |
| L05b-3 / L05b-4 / L05b-6 / G-14 | **NOT closable locally** — cloud lane, exit authority |

Everything that can be done locally is done. What remains is infrastructure and the lane, and
neither is a code gap. One unrelated defect (§2) is recorded as a strict xfail for GW05c.
