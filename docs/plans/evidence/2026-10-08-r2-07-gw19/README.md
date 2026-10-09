# R2-07 / R2-08 (+ R2-18) / GW19 evidence — 2026-10-08

Plan: `docs/plans/2026-10-08-r2-07-r2-08-gw19-admission-control.md`

| File | What it is |
|---|---|
| `gate-results.json` | The local gate pass counts recorded for this card: the `lgw19` subset, the `admit/` package, `mypy --strict`, `ruff`, `import-linter`, the AST capacity-literal gate, and the local acceptance gates (LGW19-1/2/4/5/6, G-06 local, G-15 local) with their measured numbers. |

## Recorded gate numbers

| Gate | Result |
|---|---|
| `pytest -k lgw19` | **99 passed**, 1052 deselected |
| `pytest tests/admit/` | **163 passed** |
| `mypy --strict gateway_v2` | clean, 107 source files |
| `ruff check gateway_v2` | clean |
| `import-linter` | 2 contracts kept, 0 broken (107 files, 150 dependencies) |
| AST capacity-literal gate | 3 passed (no capacity literal in `admit/` except via `ResourceContract`) |

- **LGW19-1 (3× q_safe burst):** ≈66.7 % shed; admitted p99 = 1.0 ms (SLO budget 20 ms); peak
  request-queue depth = declared cap (bounded memory); **0 FAIL_OPEN**.
- **LGW19-4:** concurrency cap binds and is observable via `queue_report` / metrics (not inert).
- **LGW19-2 (open-loop ramp):** highest-passing rate 2000 req/s; first-failing rate 2500 req/s.
- **LGW19-5 (guard rate halved mid-run):** shed rate 16.6 % → 58.2 %; queue age bounded.
- **LGW19-6 (slow consumers on 50 % of streams):** backpressure engages, memory bounded, fast
  consumers unaffected.
- **G-06 local:** amplification 1.0× (bound 1.5×) vs the 6–11 ms baseline ≥ 2.6× (≈3.0×).
- **G-15 local:** post-heal recovery ≈0.055 s (budget 10 s); `fail_open_total` 0.

## Reproduce

```bash
cd gateway_v2
.venv/bin/python -m pytest tests/ -k lgw19 -q -p no:randomly     # the card's subset
.venv/bin/python -m pytest tests/admit/ -q -p no:randomly        # the admit package
.venv/bin/python -m pytest tests/gates/test_lgw19_admit_capacity_literals.py -q
.venv/bin/mypy --strict gateway_v2
.venv/bin/ruff check gateway_v2
.venv/bin/lint-imports
```

The 10 correctness properties run as seeded `random.Random` loops of ≥ 10,000 iterations (house
idiom; no `hypothesis` dependency). The load harness is an in-process injected-clock simulation, so
every measured number is deterministic. Everything here is local-only.

## Deferred cloud / scale gates (out of local scope)

- **Full live fleet 3×-`q_safe` run** (LGW19-1 at real scale) — needs the fleet lane + a serving
  gateway (the rewrite-tree entrypoint is a stub). Represented locally by LGW19-1.
- **GW20 live `q_safe` measurement** — `q_safe` is injected; the live guard-rate measurement is
  produced by card GW20 and fed back.
- **Live G-06** against the real OpenAI SDK — represented locally by the SDK-retry-semantics sim.
- **Live G-15** post-heal certification — represented locally by the injected-clock recovery test.

The serving-entrypoint-card dependency (`--worker-connections` removal, uvicorn worker-class
migration, real OS process supervision) is listed in §4 of the plan; this card ships the
`WorkerSupervisor` seam + the observable cap only.
