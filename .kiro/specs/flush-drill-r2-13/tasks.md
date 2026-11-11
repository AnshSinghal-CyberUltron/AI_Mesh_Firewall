# Implementation Plan: local flush chaos drill + declared store-failure behaviour doc (B8 / R2-13, partial — local half)

## Overview

Convert the B8 / R2-13 (partial) design into code-generation steps that stand up the **locally
buildable half** of R2-13: a **flush chaos drill** against the already-shipped
`gateway_v2/state_control/` in-memory twins, plus a short **declared store-failure behaviour** doc.
The drill reproduces the C36 "store flush, Postgres untouched" fault — wipe the published copy, leave
the durable truth intact — and asserts that **one** re-hydrator round restores every kind from the
durable truth with **0 Restore_Violations** ("0 violations in all 15 runs", locally).

**This card ships ONLY a test file + a markdown doc — no production code.** Every mechanism the drill
exercises is already shipped (`MemoryStore` with `flush()` + the re-hydrator-only whole-kind
`publish_kind`, `MemoryControlDB` the durable twin a flush never touches, `Rehydrator.round_once`,
`StateWriter`, `ALL_KINDS` / `StateKind`). The drill rebuilds **nothing**; it generalizes the shipped
`test_lgw05b_rehydrate_stamp.py` single-example flush→restore assertion into a seeded, randomized,
multi-kind property with a deep 0-violations verify. There is NO production module to build bottom-up,
so this plan is deliberately SHORT — the smallest card so far, smaller than B6/B7.

Implementation language: **Python** — the design is grounded in concrete `gateway_v2` /
`state_control` modules and the shipped house idiom (seeded `random.Random`, no hypothesis; the
shipped `MemoryStore` / `MemoryControlDB` / `Rehydrator` / `StateWriter` APIs). The design uses Python
throughout and names concrete modules, so no implementation-language question is needed.

### The single-test-file collision decision (CRITICAL — read before scheduling)

The entire test deliverable is **ONE file**: `gateway_v2/tests/state_control/test_flush_drill_r2_13.py`.
The B6/B7 runs hit a collision whenever a single test file was split across parallel sub-agents (each
appends to the same file → clobber). To eliminate that risk here, the harness helpers + all 5 property
tests + the example/integration tests are written by a **single coherent task** (one sub-agent, one
file write), NOT split across parallel sub-tasks. The `_restored_matches_durable` helper the property
tests depend on is a non-optional implementation sub-task within that same file-owning task. The doc
is a separate file (no collision with the test). The wave graph serializes accordingly.

### Conventions enforced in the test task

- **Property tests** use the house idiom: a seeded `random.Random` driven for **≥ 10,000 iterations**,
  **NO hypothesis**. Each property test is tagged
  `# Feature: flush-drill-r2-13, Property N: <text>` and `# Validates: Requirements X.Y`, with exactly
  **one** design property ↔ **one** property-based test. The five design properties (Properties 1–5)
  map one-to-one onto five property tests, all in the one file.
- **Reuse the shipped builders, fabricate nothing.** The `_lab()` helper mirrors
  `test_lgw05b_rehydrate_stamp.py`: build `MemoryControlDB` + `MemoryStore` + `StateWriter` +
  `Rehydrator` over `ALL_KINDS` with `stale_grace_s=0.0` and a fixed `clock=lambda: 1_000.0`
  (deterministic, no sleep). `SECRET` and `_plan_body` are imported from
  `tests.state_control.test_lgw05c_writer` — no new secret, no duplicate helper. Seeding goes through
  the real `StateWriter` path (`_seed_random`), never by fabricating store contents.
- **The exact 0-violations comparison** is the design's `_restored_matches_durable(store, db) ->
  list[str]`: per kind, `stored_feed_seq == counters.feed_seq`, `index_count == count`,
  `engaged_count == on_count`, and the DEEP full per-key `stored_index` == `db.snapshot` positions;
  an empty list is 0 Restore_Violations.
- **No production code, no config change.** The card edits NO file under
  `gateway_v2/state_control/*.py`, `gateway_v2/domain/`, or `gateway_v2/runtime/`, and makes NO
  `pyproject.toml` / `[tool.importlinter]` / ruff / mypy change. A new test file + a new `.md` doc need
  none. Any helper is a small test-local function **inside the test file**.
- **Deterministic + offline**: in-memory twins only, no server, no Postgres, no Valkey; the seed fully
  determines each iteration.

### The gate (run from cwd `gateway_v2` using the `gateway_v2/.venv` venv)

All gate commands run from cwd `gateway_v2` with the `gateway_v2/.venv` venv (system python lacks
redis). Because this card adds **no production source and no config**, the gates must be
**UNCHANGED-green** — the only delta is the new test file + the new `.md` doc. The full gate is:

- `.venv/bin/python -m pytest tests/state_control/test_flush_drill_r2_13.py -q` (the drill itself)
- `.venv/bin/python -m pytest` (full suite stays green — B7's 1687-passed baseline **plus** the new
  flush-drill tests, confirming the test-only addition broke nothing)
- `.venv/bin/mypy --strict`
- `.venv/bin/ruff check gateway_v2 state_control audit_control load_harness lint tests`
- `.venv/bin/lint-imports` (the test sits outside `root_packages = ["gateway_v2"]`; contracts kept)
- the 6 AST gates: `.venv/bin/python -m lint.check_capacity_literals gateway_v2`,
  `.venv/bin/python -m lint.check_no_module_mutable gateway_v2`,
  `.venv/bin/python -m lint.check_frozen_dataclasses gateway_v2`,
  `.venv/bin/python -m lint.check_http_outside_edge_resolve gateway_v2`,
  `.venv/bin/python -m lint.check_sizes gateway_v2`,
  `.venv/bin/python -m lint.check_tenant_scale gateway_v2`
- the unscanned-channels check
  (`from gateway_v2.domain.channels import unscanned_channels; unscanned_channels() == ()`)

The `.py`-only AST gates never read a test or a `.md`; the known `valkey.py:100` `check_tenant_scale`
finding is on the live Valkey path and does not touch the in-memory drill.

## Tasks

- [x] 1. The flush chaos drill — harness, helpers, and all tests in ONE file
  - [x] 1.1 Write the drill harness + helpers + all 5 property tests + the example tests in a single file
    - **Single file-owning task — one sub-agent, one write to
      `gateway_v2/tests/state_control/test_flush_drill_r2_13.py`.** Do NOT split this across parallel
      sub-agents (the single-test-file collision risk). Create the module containing:
    - **Imports (reuse, don't duplicate):** `SECRET` and `_plan_body` from
      `tests.state_control.test_lgw05c_writer`; `MemoryStore` / `StoredHead` from
      `state_control.publisher`; `MemoryControlDB` / `KindCounters` from `state_control.db`;
      `Rehydrator` / `RoundSummary` / `RepairEvent` / `MISSING` / `ALL_KINDS` from
      `state_control.rehydrate`; `StateWriter` from the shipped writer module; `StateKind` from
      `domain.state`.
    - **`_lab()` helper (non-optional):** mirror `_lab()` in `test_lgw05b_rehydrate_stamp.py` — build
      `MemoryControlDB` + `MemoryStore` + `StateWriter(db, store, SECRET)` + `Rehydrator(db, store,
      writer, SECRET, stale_grace_s=0.0, clock=lambda: 1_000.0, kinds=ALL_KINDS)`; return
      `(writer, db, store, rehydrator)`.
    - **`_seed_random(writer, rng)` helper (non-optional):** randomized multi-kind seeding through the
      real `StateWriter` path only — bounded small counts per kind (PLAN via `plan_set`, KEY via
      `key_add` with random rate/burst, KS via `killswitch` with a random `on=` toggle so `on_count` /
      `engaged_count` is non-trivial, BUDGET via `put(StateKind.BUDGET, …)`); every kind gets ≥ 1
      write. Fabricate NO store contents directly.
    - **`_restored_matches_durable(store, db) -> list[str]` helper (non-optional):** the exact
      per-kind 0-violations comparison from the design — `stored_feed_seq == counters.feed_seq`,
      `head.index_count == counters.count`, `head.engaged_count == counters.on_count`, and the DEEP
      `dict(store.stored_index(kind)) == {record.key: record.feed_seq for record in db.snapshot(kind)
      records}`; return the list of violation strings (empty ⇒ 0 Restore_Violations).
    - _Requirements: 1.1, 1.2, 1.3, 8.2, 8.3, 8.4_
    - _Design: Components → The lab (step 1), Seeding generator (step 2), Deep verify (step 7); Data
      Models (test-local helpers only, no new value type); Architecture → Runs against shipped twins_

  - [x]* 1.2 Property test — Property 1: flush then a round restores 0 Restore_Violations
    - In the SAME file: seeded `random.Random` ≥ 10,000 iters, no hypothesis — for any seeded
      multi-kind state, `_seed_random`, `store.flush()`, one `round_once(deep=True)`, then assert
      `_restored_matches_durable(store, db) == []` (position, counts, engaged-set size, and the full
      per-key index all agree with the durable truth for every kind in `ALL_KINDS`)
    - Tag `# Feature: flush-drill-r2-13, Property 1` / `# Validates: Requirements 4.1, 4.2, 4.3, 4.4`
    - _Requirements: 4.1, 4.2, 4.3, 4.4_
    - _Design: Correctness Properties → Property 1; Components → Deep verify (step 7)_

  - [x]* 1.3 Property test — Property 2: every flushed kind repaired with reason MISSING
    - In the SAME file: seeded `random.Random` ≥ 10,000 iters, no hypothesis — for any seeded
      multi-kind state, after `store.flush()` a `round_once(deep=True)` repairs exactly
      `set(StateKind)`, each `RepairEvent.reason == MISSING`, with `summary.errors == ()` /
      `summary.ok`
    - Tag `# Feature: flush-drill-r2-13, Property 2` / `# Validates: Requirements 3.1, 3.2, 3.3`
    - _Requirements: 3.1, 3.2, 3.3_
    - _Design: Correctness Properties → Property 2; Components → The re-hydrator round (step 6)_

  - [x]* 1.4 Property test — Property 3: one-round bound, second round is a no-op
    - In the SAME file: seeded `random.Random` ≥ 10,000 iters, no hypothesis — for any seeded
      multi-kind state, the restore completes within the Declared_Flush_Bound of **one** round, and a
      second `round_once(deep=True)` immediately after performs no repair (`second.repairs == ()`,
      `second.ok`) — the bound asserted as a deterministic round count, never a wall-clock figure
    - Tag `# Feature: flush-drill-r2-13, Property 3` / `# Validates: Requirements 5.1, 5.2, 5.3`
    - _Requirements: 5.1, 5.2, 5.3_
    - _Design: Correctness Properties → Property 3; Components → Round bound (step 8)_

  - [x]* 1.5 Property test — Property 4: a flush never touches the durable truth
    - In the SAME file: seeded `random.Random` ≥ 10,000 iters, no hypothesis — for any seeded
      multi-kind state, `store.flush()` leaves the Durable_Twin unchanged: for every kind
      `db.counters(kind)` and `db.snapshot(kind)` are identical before and after the flush, so the
      restore always has the full durable state to recover from
    - Tag `# Feature: flush-drill-r2-13, Property 4` / `# Validates: Requirements 2.1, 2.3`
    - _Requirements: 2.1, 2.3_
    - _Design: Correctness Properties → Property 4; Components → Flush (step 4) + flushed-store assertion_

  - [x]* 1.6 Property test — Property 5: in-flight durable writes around a flush still restore cleanly (L05b-3, honest)
    - In the SAME file: seeded `random.Random` ≥ 10,000 iters, no hypothesis — for any seeded
      multi-kind state with additional durable writes committed around the flush (extra
      `writer.plan_set` / `writer.put`), flush then a round still restores every kind with
      `_restored_matches_durable(store, db) == []`; a docstring records that NO `FOR UPDATE` lock and
      NO queued-writer fail-fast is modelled (the in-memory twin has none — deferred to the live gate)
    - Tag `# Feature: flush-drill-r2-13, Property 5` / `# Validates: Requirements 6.1, 6.2`
    - _Requirements: 6.1, 6.2, 6.3_
    - _Design: Correctness Properties → Property 5; Components → L05b-3 flavour; Error Handling (honest
      L05b-3 limit)_

  - [x]* 1.7 Example / integration tests — specific scenarios
    - In the SAME file: (a) **MISSING-reason-per-kind** — a representative seed, flush, round → assert
      every repair reason is `MISSING` and `store.whole_kind_publishes == len(ALL_KINDS)` (the
      re-hydrator-only restore path, Req 3.2); (b) **one-round bound + second-round no-op** — the
      explicit Req-5 `second.repairs == ()` example; (c) **L05b-3 honest flavour** — a flush with
      durable writes around it still restores cleanly, with an explicit comment that no `FOR UPDATE`
      lock is modelled (Req 6); (d) **pre-flush baseline** — after seeding, `store.stored_feed_seq ==
      db.counters(kind).feed_seq` and `_restored_matches_durable` is empty pre-flush, guarding a
      known-good start (Req 1.2); and the flushed-store assertion that `stored_feed_seq is None` /
      `manifest_raw is None` per kind while durable counters survive (Req 2.2)
    - _Requirements: 1.2, 2.2, 3.2, 5.3, 6.1, 6.2_
    - _Design: Testing Strategy → Example / integration tests; Components → Pre-flush baseline, Flush +
      flushed-store assertion_

- [x] 2. The declared store-failure behaviour doc (markdown only)
  - [x] 2.1 Write `gateway_v2/state_control/docs/declared-failure-behaviour.md`
    - Markdown-only, co-located beside the component it documents (no production/config edit). Publish
      the measured + declared store-failure semantics with the design's five sections, each citing the
      exact C36 facts: (1) flush → re-hydrator restore from the durable Postgres source of truth with
      0 violations (C36: FLUSHALL restore 0.456 / 1.249 / 0.610 s, bound ≈ 2.05 s; store flush ×3
      restored 0.46–1.25 s; 10 s re-hydrator gap → only declared 503s, 0 violations in all 15 runs);
      (2) Cloud SQL failover = no data-plane impact (0 × 503 to tenants, 0 acknowledged writes lost);
      (3) Valkey planned-maintenance failover ≈ 140 declared 503s; (4) Valkey has no manual failover,
      so a data-loss drill needs Redis Standard HA or a flush drill; (5) the **local-vs-deferred drill
      table** (flush drill = delivered locally in `test_flush_drill_r2_13.py`; partition + Redis
      forced-failover = deferred G-13; Cloud SQL + store overlap = deferred G-14; ≈ 2.05 s wall-clock
      cert = deferred cloud figure)
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_
    - _Design: Components → The declared-behaviour doc (Req 7); Architecture → Why the doc is co-located_

- [x] 3. Final verification gate
  - [x] 3.1 Run the full gate — confirm the test-only addition broke nothing and gates are UNCHANGED-green
    - Ensure all tests pass, ask the user if questions arise. From cwd `gateway_v2` with the
      `gateway_v2/.venv` venv run: `.venv/bin/python -m pytest tests/state_control/test_flush_drill_r2_13.py -q`
      (the drill), `.venv/bin/python -m pytest` (full suite — B7's 1687-passed baseline + the new
      flush-drill tests), `.venv/bin/mypy --strict`,
      `.venv/bin/ruff check gateway_v2 state_control audit_control load_harness lint tests`,
      `.venv/bin/lint-imports` (contracts kept, the test outside `root_packages`), the 6 AST gates
      (`.venv/bin/python -m lint.{check_capacity_literals,check_no_module_mutable,check_frozen_dataclasses,check_http_outside_edge_resolve,check_sizes,check_tenant_scale} gateway_v2`),
      and the unscanned-channels check (`unscanned_channels() == ()`). Because the card adds no
      production source and no config, CONFIRM the gates are unchanged-green — the only delta is the
      new test file + the new `.md` doc; the known `valkey.py:100` `check_tenant_scale` finding is on
      the live Valkey path and untouched. Fix any gate failure before closing
    - _Requirements: 8.1, 8.4, 8.5_
    - _Design: Testing Strategy → Gates (Req 8.5); Architecture → Known-finding non-impact_

- [x] 4. Final checkpoint
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are the test sub-tasks (the 5 property tests + the example/integration tests);
  they are visually distinguished. The non-optional sub-tasks are the harness helpers (`_lab`,
  `_seed_random`, `_restored_matches_durable` in §1.1) and the doc (§2.1) — these the drill and the
  deliverable depend on. Top-level tasks are never marked `*`.
- **Single-test-file collision handled.** The entire test deliverable is ONE file
  (`gateway_v2/tests/state_control/test_flush_drill_r2_13.py`). §1.1 and §1.2–§1.7 all write that same
  file, so they are NOT independent and MUST NOT run in parallel — they are serialized into successive
  waves (append-only to the one module, written by one coherent owner). This is the deliberate fix for
  the B6/B7 collision that occurred when a single test file was split across parallel sub-agents. The
  doc (§2.1) is a separate file and may run in parallel with the test file; the final gate (§3.1)
  depends on both.
- The five design properties map one-to-one onto five property-test sub-tasks: P1 (§1.2 flush→round
  restores 0 violations), P2 (§1.3 every kind repaired MISSING), P3 (§1.4 one-round bound +
  second-round no-op), P4 (§1.5 flush never touches durable), P5 (§1.6 in-flight writes still restore,
  L05b-3 honest). All property tests use a seeded `random.Random` for ≥ 10,000 iterations, no
  hypothesis; one property ↔ one test; tagged `# Feature: flush-drill-r2-13, Property N` /
  `# Validates: Requirements X.Y`.
- Each task references specific requirement sub-clauses and the design component/section/property it
  satisfies; collectively the tasks cover all 9 requirements per the design's Traceability table.
- No capacity/SLO number is a field anywhere in this card; no new value type is introduced (the
  comparison reads shipped frozen types `KindCounters` / `StoredHead` / `RoundSummary` / `RepairEvent`
  directly); no HTTP type; no new import edge (the test sits outside `root_packages = ["gateway_v2"]`).
  The correctness bound is a deterministic round count, never a wall-clock figure.
- Gate checkpoints (§3.1, §4) run the full gate from cwd `gateway_v2` using the `gateway_v2/.venv` venv
  (system python lacks redis). Because the card ships only a test + a `.md`, the gates are expected
  UNCHANGED-green; the full suite must stay green: B7's 1687-passed baseline + the new flush-drill
  tests.

## Deferred — explicitly NOT tasks (out of scope, local half only)

Per Requirement 9 (and Req 6.3) and the design's Deferred section, the following are named seams with
**no** implementation task here; `tasks.md` delivers only the local, buildable half (the flush drill +
the doc):

- **The partition drill** (needs real stores) — `Deferred_Cloud_Gate`, owned by G-13 / fleet; named,
  not delivered here (Req 9.1, 9.4).
- **The Redis forced-failover drill** (needs real stores) — `Deferred_Cloud_Gate`, owned by G-13 /
  fleet; named, not delivered here (Req 9.1, 9.4).
- **The Cloud SQL + store overlap drill** — `Deferred_Cloud_Gate`, owned by GW05b (G-14); named, not
  delivered here (Req 9.2).
- **The live ≈ 2.05 s wall-clock flush-restore certification** — `Deferred_Cloud_Gate`, owned by T21 /
  fleet; the local drill asserts a deterministic round-count bound instead (Req 9.3).
- **The L05b-3 held-`FOR UPDATE` lock + queued-writer fail-fast** — deferred to the live L05b-3 / G-14
  gate; the in-memory twin has no such lock, so this card does not fake one (Req 6.2, 6.3).

This card introduces NO real-store, partition, or forced-failover behaviour (Req 9.4).

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1"] },
    { "id": 1, "tasks": ["1.2"] },
    { "id": 2, "tasks": ["1.3"] },
    { "id": 3, "tasks": ["1.4"] },
    { "id": 4, "tasks": ["1.5"] },
    { "id": 5, "tasks": ["1.6"] },
    { "id": 6, "tasks": ["1.7"] },
    { "id": 7, "tasks": ["3.1"] }
  ]
}
```
