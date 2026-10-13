# GW14d — Observability contract evidence — 2026-10-10

Plan: `docs/plans/2026-10-10-gw14d-observability-contract.md`

Card GW14d (Observability phase; depends GW14 / GW05c; required by GW20 / T20 / UI05) unifies the six
already-shipped, label-free metric producers behind one Publisher that implements the
`MetricsRegistry.exposition()` contract `edge/app.py` already consumed. Fixes **R2-10 ★ (CRITICAL)**
and **R2-11 (HIGH)**. The producers were **read, not rebuilt**.

| File | What it is |
|---|---|
| `gate-results.json` | The local gate pass counts recorded for this card: the GW14d new-test sweep, the intact `tests/edge` suite, `mypy --strict`, `ruff`, `import-linter`, the five AST gates, the 11 correctness properties (with seeds + iteration counts), the L14d-2 local scale equivalent, and the deferred cloud gates. |

## Recorded gate numbers

| Gate | Result |
|---|---|
| GW14d new test sweep (the 22 new files) | **38 passed** |
| `pytest tests/edge` (existing suite intact) | **75 passed** (shipped GW12 + 17 new GW14d edge tests) |
| `mypy --strict` (full tree: gateway_v2 + state_control + audit_control + lint) | clean, **152 source files** |
| `ruff check` (gateway_v2 state_control audit_control lint tests) | clean |
| `import-linter` | **2 contracts kept, 0 broken** (the Publisher in `edge` reading `state_control` across `root_packages` does not break layers) |
| AST gates over `gateway_v2` | each exits 0 (`capacity_literals` / `no_module_mutable` / `frozen_dataclasses` / `http_outside_edge_resolve` / `tenant_scale`) |

### The 11 correctness properties

Each is a seeded `random.Random` loop of >= 10,000 iterations (house idiom; no `hypothesis`), one
property <-> one file, printing a counterexample with its seed on failure.

| # | Property | File | Seed |
|---|---|---|---|
| 1 | No request-path raise on overflow | `tests/edge/test_publisher_overflow_property.py` | `0x0b5e0001` |
| 2 | Exposition is bounded and fully typed | `tests/edge/test_publisher_exposition_count_property.py` | `0x0b5e0002` |
| 3 | Top-K preserves the largest magnitudes (+ conservation) | `tests/edge/test_cardinality_topk_property.py` | `0x0b5e0003` |
| 4 | Windowed p99 tracks the injected window (<= 0.5 ms) | `tests/edge/test_windowed_histogram_property.py` | `0x0b5e0004` |
| 5 | Reset-safe rate omits rather than publishes a negative | `tests/edge/test_reset_safe_rate_property.py` | `0x0b5e0005` |
| 6 | Each posture code / admission reason has its own counter, distinct from policy BLOCK | `tests/edge/test_counters_distinct_property.py` | `0x0b5e0006` |
| 7 | Real zeros never absent | `tests/edge/test_real_zeros_property.py` | `0x0b5e0007` |
| 8 | Round-trip of the exposition text | `tests/edge/test_exposition_roundtrip_property.py` | `0x0b5e0008` |
| 9 | Directory capacity derives from the contract, never a literal | `tests/runtime/test_capacity_derivation_property.py` | `0x0b5e0009` |
| 10 | Event-log emission is bounded and never leaks | `tests/edge/test_event_log_property.py` | `0x0b5e000a` |
| 11 | Readiness composition gates state first | `tests/edge/test_readiness_composition_property.py` | `0x0b5e000b` |

- **L14d-2 (tenant ids at scale), local equivalent — `tests/edge/test_tenant_scale_harness.py`:**
  2,000 UUID-named tenants at the default slot capacity + 20,000 at a raised-slot contract (larger
  `connection_budget`); asserts zero metric-attributable failures across all tenant requests and all
  `/metrics` scrapes, with over-capacity observations counted exactly in the Overflow_Counter.

## Reproduce

```bash
cd gateway_v2
# the 22 new GW14d test files (property suites at >=10k iters + units + integration + scale)
.venv/bin/python -m pytest \
  tests/edge/test_publisher_overflow_property.py \
  tests/edge/test_publisher_exposition_count_property.py \
  tests/edge/test_cardinality_topk_property.py tests/edge/test_cardinality_edge.py \
  tests/edge/test_windowed_histogram_property.py \
  tests/edge/test_reset_safe_rate_property.py tests/edge/test_rates_sentinel.py \
  tests/edge/test_counters_distinct_property.py tests/edge/test_counters_vocab.py \
  tests/edge/test_real_zeros_property.py \
  tests/edge/test_exposition_roundtrip_property.py \
  tests/edge/test_event_log_property.py \
  tests/edge/test_readiness_composition_property.py tests/edge/test_readiness_probe.py \
  tests/edge/test_publisher_contract.py tests/edge/test_metrics_offload.py \
  tests/edge/test_tenant_scale_harness.py \
  tests/admit/test_metrics_series_count.py \
  tests/runtime/test_capacity_derivation_property.py \
  tests/runtime/test_holdback_metrics_series_count.py \
  tests/runtime/test_stream_metrics_series_count.py \
  -q -p no:randomly
.venv/bin/python -m pytest tests/edge -q -p no:randomly          # the whole edge suite is intact
.venv/bin/python -m mypy --strict
.venv/bin/python -m ruff check gateway_v2 state_control audit_control lint tests
.venv/bin/lint-imports                                            # 2 contracts kept
for g in capacity_literals no_module_mutable frozen_dataclasses http_outside_edge_resolve tenant_scale; do
  .venv/bin/python lint/check_$g.py gateway_v2; done              # each exits 0
```

All clocks and RNGs are injected, so every measured number is deterministic. Everything here is
local-only; the card fails closed everywhere and the request path never raises.

## Deferred cloud / scale gates (out of local scope)

- **L14d-2 full 20,000-tenant live scale** — needs a real serving gateway + the fleet lane.
  Represented locally by `tests/edge/test_tenant_scale_harness.py`.
- **L14d-4 GKE autoscaler ScalingActive** — needs a live HPA resolving the typed gauge families; the
  local half asserts the gauge families are typed `gauge`.
- **The production SLO** comes from a black-box synthetic client + provider canary (C39 retained);
  GW14d makes the server-side windowed view TRACK the client view (within 0.5 ms, Property 4).

## Honest notes

- `rc3-obs-v1` is a spec reference only (no Prometheus client library — reimplemented in the
  gateway_v2 idiom).
- The windowed-busy MECHANISM is in scope; a GPU DATA SOURCE is out — none fabricated, the gauge
  emits `# TYPE gauge` with no sample and no synthesized value.
- "Metrics shared memory" is modelled as the Publisher's in-memory `CardinalityDirectory`
  availability, not a new mmap.
- The pre-existing documented R2-05 `check_tenant_scale state_control` finding at
  `state_control/valkey.py:100` is unrelated and untouched (it is on the `state_control` tree).
- One test-harness ordering bug (the Property 4 generator) was found and fixed in the test; no
  product bug, and no property was weakened.
