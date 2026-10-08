# R2-04 evidence — the re-hydrator process, two instances, and the idle-stop exemption

Date: 2026-10-08. Card GW05b, runbook row R2-04. Companion to `bounds-reach-the-server.md`.

Clauses under test, from the runbook's R2-04 row and the GW05b card:

> runs >= 2 re-hydrators in >= 2 zones (proven safe concurrently); and never lets an idle-stop
> policy touch them

> Run >= 2 re-hydrators in >= 2 zones, and alarm when an `ok_publish_pending` write is older than
> one period.

## 1. There was no process at all

Before this, `Rehydrator` and `PostgresControlDB` were constructed only by tests. There was no
entrypoint, no loop, no DSN wiring, and no env reader — `StateKnobs` is a pure value type and the
names in the handoff §1.5 were proposals. `gateway_v2/Dockerfile` bind-mounted only
`gateway_v2/pyproject.toml`, `gateway_v2/gateway_v2` and `shared/ai_mesh_shared`, so **no image
could run a re-hydrator** even though `pyproject.toml` already declared
`include = ["state_control*"]` and `psycopg[binary]>=3.2`.

Consequence: `StateRehydratorSingleInstance`, which alarms on
`count(up{job="amf-state-rehydrator"} == 1) < 2`, had no target to count, and the two clauses above
had nothing to attach to.

## 2. Two instances running concurrently, no leader election

`python -m state_control`, twice, against one Postgres and one Valkey:

```
INFO amf.state.pg      control-plane session bounds verified: lock_timeout=250ms,
                       statement_timeout=800ms, idle_in_transaction_session_timeout=5000ms,
                       tcp_user_timeout=2000ms
INFO amf.state.service re-hydrator rehydrator-a starting in zone=zone-a: period=1000ms
                       deep_every=60 fresh=5000ms pg_grace=16000ms namespace={rv2}
INFO amf.state.service metrics on http://HP-Pavilion:9108/metrics
INFO amf.state.valkey  stamp skipped: rehydrator-b verified at 1791452084.466,
                       this round started at 1791452084.466
INFO amf.state.rehydrate freshness stamp started by rehydrator-a at 1791452084.466
```

The fourth line is the concurrency contract working: the `WATCH`-guarded `put_stamp` refused to
replace a peer's stamp of the same round rather than moving `verified_at` backwards. Both
`/healthz` endpoints returned `ok`. The stamp in the store:

```json
{"verified_at_ms":1791452089467,
 "cursors":{"budget":[1,1,1],"key":[1,1,1],"ks":[1,1,1],"plan":[1,1,1]},
 "by":"rehydrator-a","deep":false,"degraded":false,"sig":"a33c2848..."}
```

All four kinds attested, signed, and attributed. Metrics after six rounds:

```
amf_rehydration_attempts_total 6
amf_rehydration_success_total 6
amf_state_stamp_written_total 6
amf_state_stamp_races_lost_total 1      <- the healthy two-instance outcome
amf_rehydration_fault_total{category="lock_timeout"} 0
amf_rehydrator_active 1
```

`SIGTERM` produced a clean `re-hydrator rehydrator-a stopped` and exit 0.

## 3. The `ok_publish_pending` alarm now has a producer

Implemented **without a schema change**. A write is unpublished exactly when its `feed_seq` is
above what the store's manifest attests, so `oldest_unpublished_at` reads
`MIN(updated_at) WHERE kind = %s AND feed_seq > %s` — the fallback query shape the alert annotation
already named, served by the existing `amf_state_record (kind, feed_seq)` index, derived from truth
rather than from a `published` flag that could drift out of step with it.

Live, with a write whose publish was made to fail:

```
state publish failed, durable in postgres, re-hydrator will publish: injected store failure
write outcome: ok_publish_pending (durable: True published: False)

WARNING amf.state.rehydrate rehydrated kind=plan reason=stale records=2 took_ms=11.032
amf_rehydration_repair_reason_total{reason="stale"} 1
amf_state_publish_pending_oldest_seconds 0.0
```

The gauge reading 0.0 here is correct and is the designed semantics: a *healthy* re-hydrator carries
the pending write within one round, so the age stays at zero. `StatePublishPending` fires at `> 2`
seconds, which per the alert's own annotation means "no re-hydrator is picking it up". The rising
case is pinned offline by `test_an_unpublished_write_raises_the_pending_age`, where the publish keeps
failing and the age becomes non-zero.

Two properties worth recording because they were explicit design constraints:

- **It costs nothing when nothing is pending.** The query runs only when Postgres is ahead of the
  store. `test_measuring_the_pending_age_costs_nothing_when_nothing_is_pending` asserts zero calls
  on a healthy round, so the O(1) round stays O(1).
- **It cannot withhold a stamp.** A failure to measure is logged and swallowed
  (`test_a_failure_to_measure_the_pending_age_never_fails_the_round`). Losing an alarm input is
  strictly cheaper than losing the fleet's enforcement signal.

It also reuses the round's existing timestamp rather than reading the clock again — several tests
pin a round's clock consumption exactly, and an observability addition that perturbed it would be
changing behaviour in order to measure behaviour.

## 4. Idle-stop exemption, asserted rather than written down

R2-24 and runbook §3 make an idle watchdog **mandatory** on every validation environment, so R2-04's
"never lets an idle-stop policy touch them" cannot be satisfied by removing the watchdog — only by
naming the instances. Both services carry:

```yaml
labels:
  ai-mesh.idle-stop: "exempt"
  ai-mesh.role: "control-plane-rehydrator"
  ai-mesh.criticality: "fleet-availability"
```

`tests/gates/test_r2_04_deployment.py` (11 tests) asserts the exemption, the distinct identities,
the two declared zones, the restart policy, the healthcheck, `state_control`'s presence in the
image, and the single-host zone caveat. **Negative control run:** removing the label from
`state-rehydrator-b` fails the gate —

```
FAILED test_each_rehydrator_is_exempt_from_idle_stop[state-rehydrator-b]
1 failed, 10 passed
```

— and restoring it returns 11 passed. The gate bites; it is not decorative. This is the lesson the
handoff draws from GW05c, where `require_bounded_client` shipped with a docstring explaining exactly
where it belonged and an entire card went past without anyone wiring it: prose does not fail a build.

One subtlety the gate also pins: the exemption must key on **identity, never on activity**. An O(1)
round is nearly idle by design, so any CPU or request-rate heuristic classifies a healthy
re-hydrator as idle *correctly* and stops it. That is precisely how the 09:06Z ledger incident
happened to `rv-r2state-cp-1`.

## 5. Gates

| Gate | Result |
|---|---|
| offline suite (the CI shape) | **599 passed, 62 skipped** |
| live suite (real Postgres + real Valkey) | **657 passed, 4 skipped** |
| `test_r2_04_bounds.py` | 7 offline / 12 live |
| `test_r2_04_service.py` | **28 passed** |
| `tests/gates/test_r2_04_deployment.py` | **11 passed**, + negative control verified |
| both pre-existing lock drills (`:453`, `:819`) | passed, unchanged |
| 5 AST gates × 2 trees, tenant-scale | clean |
| import-linter | 2 kept, 0 broken |
| ruff / `mypy --strict` | clean, 114 files |
| `docker compose config` | both services render |

## 6. What remains, honestly

- **Zone redundancy is declared, not provided.** On one host `zone-a` / `zone-b` are labels. The
  cloud spec — two single-zone MIGs rather than one regional MIG, `min = max = 1`, one scrape job —
  is §13.3 of the plan. A regional MIG may co-locate both instances during a rebalance and silently
  void the requirement.
- **The watchdog itself is not written here.** The exemption is declared and asserted; the policy
  that must honour it belongs to R2-24's environment work.
- **L05b-3 / L05b-4 / L05b-6 / G-14 remain lane-gated.** Every bound measured locally uses a
  throwaway container; no latency is claimed from it.
- **`test_lgw05c_drills.py:453` now carries an ambiguity.** It holds its lock through
  `PostgresControlDB.tx()`, so with the bounds in force the holder is itself terminated at 5 s
  against an assertion of `< 5.0` s. It passes, but tighten it when L05b-3 is written properly with
  a 60 s hold from an unbounded session.
