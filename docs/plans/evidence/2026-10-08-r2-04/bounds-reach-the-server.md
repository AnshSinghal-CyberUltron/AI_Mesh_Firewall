# R2-04 evidence — the four session bounds now reach the server

Date: 2026-10-08. Card GW05b, runbook row R2-04 (HIGH).

Runbook clause under test:

> GW05b also sets `lock_timeout`, `statement_timeout`, `idle_in_transaction_session_timeout` and
> `tcp_user_timeout` on **every control-plane connection**

and the card bullet:

> Postgres connections set lock, statement, idle-in-transaction and TCP timeouts.

## Topology

Throwaway containers, torn down after the run. PgBouncer configured with the **same** settings the
repository mandates for every application container (`docker-compose.yml:43,51`,
`deploy/pgbouncer/pgbouncer.ini:17,26`):

```
pool_mode = transaction
IGNORE_STARTUP_PARAMETERS = extra_float_digits,options
```

- `postgres:16-alpine`
- `edoburu/pgbouncer:v1.23.1-p1` — the image pinned in `docker-compose.yml:38`

## 1. The defect was real: PgBouncer discarded all four bounds

`PostgresControlDB` constructed the bounds correctly and passed them as the libpq `options`
startup parameter. Through the pooler, with `SET LOCAL` suppressed to reproduce the shipped
behaviour, the server reports `0` — no limit — for every one of the four:

```
[OLD behaviour] startup `options` only (SET LOCAL suppressed):
   DROPPED: idle_in_transaction_session_timeout: expected 5000, server reports 0
   DROPPED: lock_timeout: expected 250, server reports 0
   DROPPED: statement_timeout: expected 800, server reports 0
   DROPPED: tcp_user_timeout: expected 2000, server reports 0

[NEW behaviour] SET LOCAL authoritative:
   lock_timeout = 250ms  APPLIED
   statement_timeout = 800ms  APPLIED
   idle_in_transaction_session_timeout = 5000ms  APPLIED
   tcp_user_timeout = 2000ms  APPLIED
```

## 2. The behavioural consequence

A 300 ms `statement_timeout` against `SELECT pg_sleep(2)`, through the pooler:

```
[OLD] options only:     pg_sleep(2) COMPLETED in 2.00s -> the 300ms deadline did NOT bite
[NEW] SET LOCAL:        QueryCanceled after 0.30s      -> deadline enforced
```

So before this change the runbook clause was satisfied in form and unmet in fact, in the only
deployment topology the repository endorses. Nothing detected it because nothing read the settings
back — `verify_bounds()` now does, and fails start-up on a mismatch.

## 3. `lock_timeout` fires behind a held row lock

`test_lock_timeout_actually_fires_behind_a_held_row_lock`: a session carrying none of our options
holds `FOR UPDATE` on the `plan` counter row (so `idle_in_transaction_session_timeout` cannot end
the wait for us), and a queued writer is bounded by `lock_timeout` alone. At 250 ms the writer
raises `LockNotAvailable` in well under 2 s, consistent with the runbook's reference figure of
2.25 s at the shipped 2000 ms.

The re-hydrator is deliberately absent from that test: its publish snapshot takes no lock, which is
the already-shipped half of R2-04 and is drilled in `test_lgw05c_drills.py:453,819`.

## 4. Gates

| Gate | Result |
|---|---|
| offline suite (`AMF_PG_DSN` unset — the CI shape) | **560 passed, 62 skipped** |
| live suite (real Postgres + real Valkey) | **618 passed, 4 skipped** |
| new `test_r2_04_bounds.py` offline | 7 passed, 5 skipped |
| new `test_r2_04_bounds.py` live | **12 passed** |
| both existing lock drills (`:453`, `:819`) | **passed, unchanged** |
| 5 AST gates × 2 trees | clean |
| `check_tenant_scale gateway_v2` | clean |
| import-linter | 2 kept, 0 broken |
| ruff | all checks passed |
| `mypy --strict` | no issues, 111 files |

## 5. Note on `SET LOCAL` and the REPEATABLE READ snapshot

`SET` does **not** take a transaction snapshot in PostgreSQL, so issuing the bounds as the first
statements of `snapshot()` leaves the isolation semantics untouched: the repeatable-read snapshot is
still taken at the first statement that actually reads. The live test
`test_the_bounds_hold_on_the_read_only_snapshot_connection_too` confirms the bounds apply on the
read-only path, and the 618-test live suite confirms every existing snapshot-consistency assertion
still holds.

(An earlier draft of the plan claimed the snapshot moved earlier. That was wrong and is corrected
in the plan document.)

## 6. What this does NOT cover

Still open against the runbook, unchanged by this run:

- `≥ 2 re-hydrators in ≥ 2 zones` — no rehydrator process exists; `state_control` is not in the
  image (`gateway_v2/Dockerfile:16-18`).
- `never lets an idle-stop policy touch them` — no deployment to protect.
- `alarm when an ok_publish_pending write is older than one period` — alert exists
  (`StatePublishPending`), no producer.
- L05b-3 / L05b-4 / L05b-6 / G-14 are lane-gated; every bound measured locally uses a throwaway
  container and no latency is claimed from it.
