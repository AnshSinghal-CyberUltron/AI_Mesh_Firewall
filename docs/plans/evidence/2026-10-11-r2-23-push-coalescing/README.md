# R2-23 — Push-coalescing evidence — 2026-10-11

Plan: `docs/plans/2026-10-11-r2-23-push-coalescing.md`

Register item R2-23 (C43 push cost; depends GW05c; fix before GW19; MEDIUM) adds a per-owner,
publish-side nudge coalescer to the control-plane `state_control/` package and VERIFIES that the
original "72 pushes per request at 36 workers" defect is already structurally closed by GW05c.

| File | What it is |
|---|---|
| `gate-results.json` | The local gate pass counts: the R2-23 new-test sweep, the full state_control suite, mypy --strict, ruff, import-linter, the capacity-literals gate, the 10 correctness properties (with seeds), the microbenchmark result, and the deferred knee re-measure. |

## Recorded gate numbers

| Gate | Result |
|---|---|
| R2-23 new tests (10 property + 6 verification + 7 microbench) | **23 passed** |
| `pytest tests/state_control` (full suite, regression) | **188 passed**, 58 skipped, 1 xfailed |
| `mypy --strict` (full tree) | clean, **153 source files** |
| `ruff check` (gateway_v2 state_control audit_control lint tests) | clean |
| `import-linter` | **2 contracts kept, 0 broken** |
| capacity-literals AST gate over `gateway_v2` | PASS (StateKnobs defaults are named constants) |

### The 10 correctness properties

Each is a seeded `random.Random` loop of >= 10,000 iterations (house idiom; no `hypothesis`), one
property per test, in `tests/state_control/test_coalescer_properties.py`.

| # | Property | Seed |
|---|---|---|
| 1 | Per-owner push bound; last push carries max feed_seq | `0xc0a01` |
| 2 | Durable writes equal N; durable state byte-identical to the bare path | `0xc0a02` |
| 3 | Reader (nudges + one backstop round) ends at or above latest feed_seq | `0xc0a03` |
| 4 | Monotonic announced position; no regressive push on the wire | `0xc0a04` |
| 5 | Per-owner isolation across interleaved multi-owner workloads | `0xc0a05` |
| 6 | Idempotent flush on a quiesced owner | `0xc0a06` |
| 7 | Fail-safe: injected push failure / shutdown never costs durability or freshness | `0xc0a07` |
| 8 | Bound derives from the authority; over-long window refused at construction | `0xc0a08` |
| 9 | Confluence of write ordering (equal durable state + final announced position) | `0xc0a09` |
| 10 | Push count independent of simulated worker / request count | `0xc0a10` |

### Microbenchmark (Req 8) — `tests/state_control/test_lgw23_microbench.py`

Local, deterministic, in-process (MemoryStore twin + injected clock, zero network), byte-reproducible
on a fixed seed. On a seeded ~72-write per-owner burst workload: **un-coalesced 72 pushes ->
coalesced 26 pushes = 46 pushes saved**; Durable_Write parity **72 == 72 (OK)**; CPU-ms-shape proxy
in the R2-23 baseline's units (per-push shape, NOT a wall-clock measurement). The file declares the
deferred fleet Knee_Re_Measure OPEN and a test guards that nothing marks it satisfied/passed/closed.

### Verification obligation (Req 1) — `tests/state_control/test_push_verification.py`

Standing tripwire that the per-request/per-worker defect stays closed: one `PUBLISH` per record
write; exactly one per batch independent of record count; an AST guard that no serving package
(edge/admit/dispatch/egress) reaches the write-side publish surface; total push count a function of
W (writes) and O (owners) only, invariant to simulated worker count N and request count R.

## Reproduce

```bash
cd gateway_v2
.venv/bin/python -m pytest \
  tests/state_control/test_coalescer_properties.py \
  tests/state_control/test_push_verification.py \
  tests/state_control/test_lgw23_microbench.py \
  -q -p no:randomly                                   # the 23 R2-23 tests
.venv/bin/python -m pytest tests/state_control -q -p no:randomly   # full suite, no regression (slow: ~66s)
.venv/bin/python tests/state_control/test_lgw23_microbench.py      # standalone microbench report
.venv/bin/python -m mypy --strict
.venv/bin/python -m ruff check gateway_v2 state_control audit_control lint tests
.venv/bin/lint-imports                                             # 2 contracts kept
.venv/bin/python lint/check_capacity_literals.py gateway_v2        # exits 0
```

All clocks are injected and the stores are in-memory twins, so every number is deterministic.
Everything here is local-only; durability is never coalesced and the write outcome never changes on
a push failure.

## Deferred cloud gate (out of local scope)

- **Knee_Re_Measure (Req 9)** — fleet-scale push-cost re-measurement at the capacity knee (36 workers
  / real Valkey / real request load). OPEN. The microbenchmark is its local representative and does
  NOT substitute for the live measurement.

## Honest notes

- The "72 pushes per request" defect is already closed by GW05c; R2-23 VERIFIES it (Req 1 tripwire)
  and builds only the per-owner coalescer the correction names.
- Two test-harness bugs (counting the window-close catch-up as an in-window push) were found and
  fixed in the tests; no product bug, no property weakened — the coalescer matches design §4.
- The pre-existing documented R2-05/GW05c `check_tenant_scale state_control` finding at
  `state_control/valkey.py:100` is unrelated and untouched.
