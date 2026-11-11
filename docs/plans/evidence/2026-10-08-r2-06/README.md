# R2-06 / GW12b evidence — 2026-10-08

Plan: `docs/plans/2026-10-08-r2-06-gw12b-bounded-holdback.md`

| File | What it is |
|---|---|
| `gate-results.json` | The local gate pass counts recorded for this card: full offline suite, the `lgw12b` subset, `mypy --strict`, `ruff`, `import-linter`, and the three local gates (L12b-1 replay, L12b-3 detector regression, L12b-2 local concurrency equivalent). |

## Recorded gate numbers

| Gate | Result |
|---|---|
| `pytest` (offline, full suite) | **955 passed**, 93 skipped, 1 xfailed |
| `pytest -k lgw12b` | **141 passed**, 908 deselected |
| `mypy --strict` | clean, 102 source files |
| `ruff check gateway_v2` | clean |
| `import-linter` | 2 contracts kept, 0 broken |

- **L12b-1 replay:** 24/24 class-by-chunk-size combinations pass; max word-class hold ≤ 1–2 tokens
  (cap 3); inspected bytes = 64 = Window; per-chunk p99 ≈ 14–37 µs (bound 0.5 ms).
- **L12b-3 detector regression:** 8 tests; raw sensitive value never leaked across 5 classes × ≥ 3
  split offsets; byte-identical trade-off outcome at every offset.
- **L12b-2 local equivalent:** co-running stream median per-chunk latency delta ≈ 0 ms (bound
  0.5 ms); O(window) isolation.

## Reproduce

```bash
cd gateway_v2
.venv/bin/python -m pytest tests/ -k lgw12b -q -p no:randomly    # the card's subset
.venv/bin/python -m pytest tests/ -q -p no:randomly              # the full offline suite
.venv/bin/mypy --strict gateway_v2
.venv/bin/ruff check gateway_v2
.venv/bin/lint-imports
```

The 8 correctness properties run as seeded `random.Random` loops of ≥ 10,000 iterations (house
idiom; no `hypothesis` dependency). Everything here is local-only.

## Deferred cloud gates (out of local scope)

- **L12b-2** — the full 200 RPS fleet capacity run. Needs the fleet lane + a serving gateway
  (`edge/app.py` is a GW12 stub). Represented locally only by the scaled-down concurrency test.
- **GW20b** — fleet certification. No cloud resources reachable.

Both are listed in §4 of the plan.
