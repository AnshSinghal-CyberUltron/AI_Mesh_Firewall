# R2-09 / R2-14 / R2-19 / GW06 evidence — 2026-10-08

Plan: `docs/plans/2026-10-08-r2-09-r2-14-r2-19-gw06-budget-lease.md`

| File | What it is |
|---|---|
| `gate-results.json` | The local gate pass counts recorded for this card: the `lgw06` subset, the `admit/` package, the full offline suite, `mypy --strict`, `ruff`, `import-linter`, the AST gate suite, and the local acceptance equivalents (LGW06-3, LGW06-5, the R2-09 partition behaviour, the 45 s partition regression) with their shapes. |

## Recorded gate numbers

| Gate | Result |
|---|---|
| `pytest tests/` (full offline suite) | **1131 passed**, 93 skipped, 1 xfailed, 0 failed |
| `pytest -k lgw06` | **74 passed**, 1151 deselected |
| `pytest tests/admit/` | **223 passed** |
| `mypy --strict gateway_v2` | clean, 109 source files |
| `ruff check gateway_v2 tests` | clean |
| `import-linter` | 2 contracts kept, 0 broken (109 files, 154 dependencies) |
| AST gate suite (`tests/gates/`) | 79 passed (capacity-literal, sizes, http, frozen, mutable, import_linter) |

- **LGW06-3 (multi-replica, no quota multiplication):** N in-process `BudgetLease` replicas over one
  `fakeredis` pool, ≥ 10,000 arrivals; aggregate admission ≤ limit + overshoot; overshoot published
  and read back; aggregate strictly below `limit × workers` (no Replica_Multiplication).
- **LGW06-5 (injected 200 ms latency):** bounded-timeout contract bites (modelled 200 ms op > the
  bounded per-op timeout); declared posture holds (admit → `budget_unavailable` on exhaustion); pool
  bounded (request path creates zero connections, advances the clock by zero — no store op on the
  hot path).
- **R2-09 partition behaviour:** spends the Remaining_Lease then returns `budget_unavailable` (never
  `shared_state_unavailable`); request-path store-call counter stays 0 (refill never on the request
  path).
- **45 s partition regression (R2-19 / R2-09, Property 7):** D2 listener surfaces the keepalive
  timeout and does not spin to unbounded memory; D1 dead socket reconnects rather than answering
  500; R2-09 budget posture holds with no request-path refill read.

The 10 correctness properties run as seeded `random.Random` loops of ≥ 10,000 iterations (house
idiom; no `hypothesis` dependency), async via `asyncio.run`. Benign known warning:
`RuntimeWarning: coroutine 'BudgetLease._refill' was never awaited` in the lease property tests
(undrained single-flight refill coroutines) — not a failure.

## Reproduce

```bash
cd gateway_v2
.venv/bin/python -m pytest tests/ -k lgw06 -q -p no:randomly              # the card's subset
.venv/bin/python -m pytest tests/admit/ -q -p no:randomly                 # the admit package
.venv/bin/python -m pytest tests/ -q -p no:randomly                       # the full offline suite
.venv/bin/python -m pytest tests/gates/ -q -p no:randomly                 # the AST gate suite
.venv/bin/mypy --strict gateway_v2
.venv/bin/ruff check gateway_v2 tests
.venv/bin/lint-imports
```

Everything here is local-only; the harness is an in-process injected-clock simulation over
`fakeredis`, so every measured number is deterministic.

## Deferred cloud / scale gates (out of local scope)

- **Full live four-replica fleet quota run** (the full LGW06-3 at real scale) — needs the fleet lane
  + a serving gateway (the rewrite-tree entrypoint is a stub). Represented locally by LGW06-3.
- **Live store failover (LGW06-6)** — needs a real Valkey / Memorystore primary failover. Represented
  locally by the `fakeredis` partition equivalents.
- **Real cross-zone RTO measurement** — the ~200 ms minimum TCP RTO R2-14 addresses is modelled here
  as a deterministic injected-clock latency, not measured against real cross-zone sockets.

The **R2-14 zone placement** (gateways placed in the store primary's zone) is a **deployment
dependency, not code**; the code-side requirement is the retry-once-on-idempotent-read-timeout,
which is implemented and tested (Property 6). **R2-19** was **confirm + regression-test only** — the
`state_nudge` D2 fix already existed and `NudgeListener` was left intact; no D1 gap was found in
`store_valkey.py`, so no guard was added.
