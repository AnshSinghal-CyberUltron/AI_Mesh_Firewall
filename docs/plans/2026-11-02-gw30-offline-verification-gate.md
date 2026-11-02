# GW30 — Vector Store Adapter: Final Offline Verification Gate (task 15.1)

Spec: `.kiro/specs/vector-store-adapter-gw30/` (requirements-first, feature).
Task: 15.1 "Run the full offline gate and reconcile evidence."
Date: 2026-11-02.
Evidence: `docs/plans/evidence/2026-11-02-gw30-offline-gate/`.

This card is a **GW card, not an R2 item** — per the spec Notes, it does **not**
create the four-memory Ruflo/Cursor/AGENTS.md/CHANGELOG mirrors. This plan + the
evidence directory are the recorded offline exit evidence.

## Scope of task 15.1

Run the full offline gate for the GW30 adapter layer and reconcile evidence:
the full `gateway_v2` pytest suite + the `lgw30` subset; `mypy --strict`; `ruff`;
`lint-imports`; and all AST gates (`check_capacity_literals`,
`check_no_module_mutable`, `check_frozen_dataclasses`,
`check_http_outside_edge_resolve`). Fix any gate failure caused by GW30; record
the output. The live gates (L30-1/L30-2/L30-3, task 16.1) are explicitly out of
scope here.

## GW30 change surface (git working tree)

Modified: `gateway_v2/domain/posture.py`, `gateway_v2/runtime/resources.py`.
Added:   `gateway_v2/dispatch/vector_adapter.py`, `vector_bounded_op.py`,
`vector_connectors.py`, `vector_credentials.py`, `vector_leak_guard.py`,
`vector_operations.py`, `vector_registry.py`; `gateway_v2/runtime/resources_vector.py`;
`tests/dispatch/test_lgw30_*.py`, `tests/domain/test_lgw30_posture.py`,
`tests/runtime/test_lgw30_resources.py`.

## Gate results — all GATES PASS

| # | Gate | Command (cwd = `gateway_v2/`) | Result |
|---|------|-------------------------------|--------|
| 1 | GW30 `lgw30` test sweep | `pytest tests/dispatch/test_lgw30_*.py tests/domain/test_lgw30_posture.py tests/runtime/test_lgw30_resources.py -q` | **PASS — 76 passed** (43.2s) |
| 2 | Full offline suite | `pytest -q` | **PASS for GW30 — 2772 passed, 100 skipped, 1 xfailed; 2 failed are pre-existing + unrelated (0 new regressions)** |
| 3 | `mypy --strict` | `mypy --strict` | **PASS — no issues in 252 source files** |
| 4 | `ruff` | `ruff check gateway_v2` | **PASS — All checks passed!** |
| 5 | `lint-imports` | `lint-imports` | **PASS — 3 contracts kept, 0 broken** (241 files, 1369 deps) |
| 6a | `check_capacity_literals` | `python -m lint.check_capacity_literals gateway_v2/gateway_v2` | **PASS — exit 0** |
| 6b | `check_no_module_mutable` | `python -m lint.check_no_module_mutable gateway_v2/gateway_v2` | **PASS — exit 0** |
| 6c | `check_frozen_dataclasses` | `python -m lint.check_frozen_dataclasses gateway_v2/gateway_v2` | **PASS — exit 0** |
| 6d | `check_http_outside_edge_resolve` | `python -m lint.check_http_outside_edge_resolve gateway_v2/gateway_v2` | **PASS — exit 0** |
| 6e | `check_sizes` (supplemental) | `python -m lint.check_sizes gateway_v2/gateway_v2` | **PASS — exit 0** (confirms `runtime/resources*.py` within size gate after the GW30 derivations) |

### lint-imports contract confirmation (GW30-relevant)
- `Gateway v2 layers` KEPT — `dispatch` stays the adapter home; no `edge`/`dispatch`
  → downward connector/credential edge; layer order intact.
- `Resolve imports only domain` KEPT.
- `No HTTP framework outside edge` KEPT — codes-only posture additions carry no
  HTTP object (`check_http_outside_edge_resolve` exit 0 corroborates).

## The 2 full-suite failures are PRE-EXISTING and UNRELATED to GW30

GW30 is a Python-only adapter layer; it adds no `edge/` code, no frontend JS, and
no console-inventory entry. Both failures sit entirely outside the GW30 change
surface (see `02_full_offline_suite.txt` + `02b_preexisting_failures_detail.txt`).

1. `tests/gates/test_check_tenant_scale.py::test_the_shipped_data_plane_is_clean`
   — violations in `gateway_v2/edge/dev_components.py:308` (+1), from the prior
   GW26 dev-profile work (git `543b2993` / `f56160c1`). Matches the standing
   AGENTS.md check_tenant_scale finding. No GW30 module in the violation list.

2. `tests/lint/test_console_inventory.py::test_checked_in_inventory_matches_the_tree`
   — all deltas are `frontend/src/**` JS reads vs the checked-in inventory and
   the `docs/plans/evidence/2026-10-26-gw14b/` JSON (GW14b frontend verifier).
   GW30 adds no JS and no inventory entry.

Both are out of scope for task 15.1 (which targets GW30 gate failures) and were
left untouched. No GW30-caused failure was found, so no fix was required.

## Verdict

Every GW30 offline gate PASSES. 0 new regressions. Task 15.1 offline evidence
is recorded under `docs/plans/evidence/2026-11-02-gw30-offline-gate/`. The card
does not exit on unit tests alone — the live acceptance gates L30-1/L30-2/L30-3
(task 16.1, real Docker/staging + real wire bytes) remain the exit evidence and
are deliberately out of this task's scope.
