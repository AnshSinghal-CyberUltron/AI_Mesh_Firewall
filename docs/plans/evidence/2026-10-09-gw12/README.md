# GW12 — SSE egress pipeline evidence — 2026-10-09

Plan: `docs/plans/2026-10-09-gw12-sse-egress-pipeline.md`

Card GW12 (Protocol phase; depends GW03 / GW11; required by GW13 / GW14 / GW16b / GW19) stands up
the serving skin around the already-shipped bounded-holdback engine (GW12b / R2-06), which this card
**wired into, not rebuilt**.

| File | What it is |
|---|---|
| `gate-results.json` | The local gate pass counts recorded for this card: the full offline suite, the `lgw12` subset, `mypy --strict`, `ruff`, `import-linter` (no `egress → detect` edge), the `tests/gates/` AST suite + each AST gate CLI, the 10 correctness properties, and the local acceptance equivalents (LGW12-1 plateau, LGW12-7 conformance) with their deferred cloud counterparts. |

## Recorded gate numbers

| Gate | Result |
|---|---|
| `pytest tests/` (full offline suite) | **1194 passed**, 93 skipped, 1 xfailed, 0 failed |
| `pytest -k lgw12` | **205 passed**, 1083 deselected |
| `mypy --strict gateway_v2` | clean, 117 source files |
| `ruff check gateway_v2 tests` | clean |
| `import-linter` | 2 contracts kept, 0 broken (117 files, 211 dependencies); no `egress → detect` edge |
| `pytest tests/gates/` (AST gates) | 78 passed |
| AST gate CLIs | each exits 0 (`capacity_literals` / `no_module_mutable` / `frozen_dataclasses` / `http_outside_edge_resolve` / `tenant_scale`) |

- **The 10 correctness properties** each run as a seeded `random.Random` loop of ≥ 10,000 iterations
  (house idiom; no `hypothesis` dependency), async paths via `asyncio.run`. One property ↔ one test,
  tagged `# Feature: sse-egress-pipeline, Property N` + `# Validates: Requirements X.Y`.
- **LGW12-1 (memory plateau), local equivalent — `tests/egress/test_lgw12_concurrency.py`:** N
  in-process streams assert `total buffered ≤ active_streams × stream_buffer_bytes(active_streams)`
  and buffered memory does not grow with stream length.
- **LGW12-7 (both-SDK conformance), local equivalent — `tests/edge/test_lgw12_asgi_conformance.py`:**
  in-process ASGI transport with recorded-frame checks for the `data: [DONE]` terminal marker, the
  `Error_Frame` shape, and split-surrogate decoding.

A known-benign warning surfaces in the full-suite run: R2-09's `coroutine 'BudgetLease._refill' was
never awaited` — **unrelated to GW12**, carried from the GW06 card, not a failure.

## Reproduce

```bash
cd gateway_v2
.venv/bin/python -m pytest tests/ -q -p no:randomly              # full offline suite
.venv/bin/python -m pytest tests/ -k lgw12 -q -p no:randomly     # the card's subset
.venv/bin/python -m pytest tests/gates/ -q -p no:randomly        # the AST gate suite
.venv/bin/mypy --strict gateway_v2
.venv/bin/ruff check gateway_v2 tests
.venv/bin/lint-imports                                           # assert no egress -> detect edge
```

All clocks, RNGs, scanners and the provider socket are injected (the provider is
`StubProviderClient`, in-process, no socket), so every measured number is deterministic. Everything
here is local-only; the pipeline fails closed everywhere and the `FAIL_OPEN` counter stays zero.

## Deferred cloud / scale gates (out of local scope)

- **LGW12-1 full 1,000-stream memory plateau** — needs the fleet lane + a real serving gateway.
  Represented locally by `tests/egress/test_lgw12_concurrency.py`.
- **LGW12-7 real-TCP both-SDK conformance** (OpenAI Python + Node) — needs real TCP sockets + both
  SDKs. Represented locally by `tests/edge/test_lgw12_asgi_conformance.py`.
- **200 RPS / fleet certification** (full live LGW12-1/2/3) — a cloud gate; represented locally by
  the scaled-down in-process harness.

## GW13 follow-ups and honest notes (see §4 of the plan)

- The per-delta holdback scan stays **inline** in the shipped `StreamPipeline` release loop
  (offloading it would re-architect the shipped GW12b loop — out of scope). The error-frame scan
  **is** offloaded and `/metrics` **is** off-loop.
- The streaming chat handler is **thin by design** — full `/v1/chat/completions` semantics are
  GW15 / GW16; `STRICT_WITHHOLD` + the `INCREMENTAL` mode switch are GW13 (`egress/strict.py` still
  stubbed).
- A fail-closed defect found during Property-7 work is now **fixed** (abort that raises within the
  bound still releases the buffer, not misreported as bound-exceeded).
- The GW05b edge-stub tripwire test was **deleted** per its own instruction now the edge layer
  exists; its 14 sibling handoff tests still pass.
- No real HTTP framework was added (raw ASGI 3.0 app); the real provider socket is a stub — a real
  upstream client is a deferred live gate.
