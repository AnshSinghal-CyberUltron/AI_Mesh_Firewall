# Implementation Plan: OpenAI Conformance GW01 (local half)

## Overview

This plan implements the approved Option-2 design: the ported OpenAI conformance suite in
`gateway_v2/tests/openai_conformance/` runs green against **both** apps (`AMF_CONFORMANCE_APP=v1`
default and `=v2`) by splitting the suite into a v1 arm (World A) and a v2 arm (World B), both
calling one shared wire-contract assertion helper that reads a resolver-aware
Expected_Diff_Ledger.

Work is ordered **bottom-up by layer** so each wave is independently verifiable, matching the house
cadence:

- **WAVE 0 — product (lowest layer):** new `expected_diffs.py` ledger + version pins, and the
  `harness.py` `expected_diff()` extension. These hit `mypy --strict` + the AST gates.
- **WAVE 1 — shared test helper:** `wire_contract.py` (depends on the resolver + ledger).
- **WAVE 2 — collection gate:** `conftest.py` rewrite (depends on resolver).
- **WAVE 3 — v1 arm fixes:** lazy-import conversions + single-identity accessor (depend on
  conftest + resolver).
- **WAVE 4 — v2 arm reconcile:** re-point v2 subsets at the shared helper + fix genuine local gaps
  (depends on `wire_contract.py`).
- **WAVE 5 — verification:** run both arms, directory==sum-of-files, then the full Gate_Obligation.

Repo `/home/suraj/Documents/zeroshield/AI_Mesh_Firewall`, branch `suraj-revamp`. All commands run
from the `gateway_v2/` directory against `.venv`.

**Task-kind legend:** tasks tagged **[product-code]** touch `gateway_v2.contracts` modules and are
subject to `mypy --strict`, `lint-imports`, and the 7 AST gates (frozen dataclass, capacity-literal,
size limits). Tasks tagged **[test-file]** edit files under `gateway_v2/tests/` only.

## Tasks

- [x] 1. WAVE 0 — Expected_Diff_Ledger + resolver extension (product, lowest layer)
  - [x] 1.1 Create the Expected_Diff_Ledger product module **[product-code]**
    - Create `gateway_v2/gateway_v2/contracts/openai_conformance/expected_diffs.py`
    - Define the frozen `ExpectedDiff` dataclass with `@dataclass(frozen=True, slots=True)` and
      fields `key: str`, `v1: object`, `v2: object`, `runbook_row: str`
    - Define module constants `SDK_FROZEN_VERSION = "2.38.0"` and `SDK_INSTALLED_FLOOR = "3.29.0"`
    - Define a frozen mapping `key -> ExpectedDiff` encoding the SEVEN §10.2.2 rows:
      `injection_detection`, `benign_inline_code`, `degraded_scan_status`, `policy_precedence`,
      `tri_state_one_plan`, `plan_unavailable`, `stream_block_disposition`, each with its `v1`
      expected value, `v2` expected value, and `runbook_row` provenance string
    - Depend only on the standard library (no higher-layer imports); keep version strings and
      behaviour expectations only (no capacity literals)
    - _Requirements: 6.1, 4.4_

  - [x] 1.2 Extend the App_Resolver with the ledger reader **[product-code]**
    - Modify `gateway_v2/gateway_v2/contracts/openai_conformance/harness.py`
    - Add `expected_diff(key: str) -> ExpectedDiff` that reads the ledger and returns the value
      expected for the app `conformance_app_name()` currently selects (so a cell never branches on
      the app name itself)
    - Keep the existing fail-closed `conformance_app_name()` / `tcp_base_url()` / `is_tcp_mode()`
      unchanged
    - Import `ExpectedDiff` and the ledger mapping from `expected_diffs.py` (same-layer, no higher
      layer)
    - _Requirements: 6.2, 5.3, 5.4_

  - [x] 1.3 Verify WAVE 0 product layer **[product-code]**
    - Run `cd gateway_v2 && .venv/bin/mypy --strict gateway_v2/contracts/openai_conformance/` with
      zero errors
    - Run the frozen-dataclass AST gate and the capacity-literal AST gate over the new module and
      confirm both exit zero (`ExpectedDiff` is `frozen=True, slots=True`; no capacity literal
      outside `runtime/resources.py`)
    - Run `cd gateway_v2 && .venv/bin/ruff check gateway_v2/contracts/openai_conformance/` clean
    - _Requirements: 7.2, 7.3, 7.5_

- [x] 2. WAVE 1 — shared wire-contract assertion helper (test layer)
  - [x] 2.1 Create the shared wire-contract helper **[test-file]**
    - Create `gateway_v2/tests/openai_conformance/wire_contract.py`
    - Implement `assert_chat_completion(completion)` (typed `ChatCompletion` shape + usage block),
      `assert_stream_frames(chunks)` (chunk framing + terminal `[DONE]` semantics),
      `assert_error_envelope(err, *, expected_code: str)` (`message`/`type`/`code`/`param`/
      `request_id` + HTTP status), and `assert_done_terminates(stream)` (`[DONE]` ends iteration
      cleanly)
    - Assert ONLY wire-visible HTTP/SSE facts; name NEITHER `ai_mesh_gateway` NOR `gateway_v2`
      anywhere in the module
    - For any helper touching a deliberate §10.2.2 difference (e.g. streaming-block disposition,
      content-filter status/code mapping, degraded posture), draw the expected value from
      `expected_diff(...)` via the resolver rather than a hard-coded v1 constant
    - _Requirements: 5.3, 4.2, 6.2_

- [x] 3. WAVE 2 — conftest collection gate rewrite (test layer)
  - [x] 3.1 Rewrite the collection gate **[test-file]**
    - Modify `gateway_v2/tests/openai_conformance/conftest.py`
    - Remove the `skip_v2 = pytest.mark.skip(reason="v2 has no OpenAI surface yet (GW15)")` marker
      and every use of it; the module must contain no skip reason referencing the absence of a v2
      OpenAI surface
    - Keep the TCP-vs-ASGI separation verbatim: when `tcp_base_url()` is set, skip the in-process
      ASGI cells (`sdk_app` / `sdk_client` fixtures) as a separate job
    - Invert the gate: when `conformance_app_name()` returns `v2`, mark the v1-app cells (identified
      by the `sdk_app` / `sdk_client` fixtures) skip with reason
      `"v1-app cell; v2 wire contract proved by the v2 arm"` so they collect cleanly but do not
      drive the v1 app under a v2 run
    - Under `v1`/unset, the v1-arm cells run as today with no change in collected/passing cells
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

- [x] 4. WAVE 3 — v1 arm collection-error + dual-identity fixes (test layer)
  - [x] 4.1 Convert the phase2 file to lazy/resolver-gated imports **[test-file]**
    - Modify `gateway_v2/tests/openai_conformance/test_openai_sdk_compat_phase2.py`
    - Move `from ai_mesh_gateway.responses_adapters import responses_to_chat` (and any top-level
      `ai_mesh_gateway.main` import) behind the existing `_resolved_main()`-style lazy, resolver-
      aware accessor at its fixture/call site, not module top level
    - Result: zero collection errors under `v2`; zero reduction in v1 cell count under `v1`/unset
    - _Requirements: 2.1, 2.3, 2.4_

  - [x] 4.2 Convert the live-uvicorn file to lazy/resolver-gated imports **[test-file]**
    - Modify `gateway_v2/tests/openai_conformance/test_openai_sdk_compat_live_uvicorn.py`
    - Move the three top-level v1 imports (`ai_mesh_gateway.main`, `ai_mesh_gateway.middleware`,
      `ai_mesh_gateway.scanner`) AND the module-level `from tests.openai_conformance import
      test_openai_sdk_compat as T` binding behind lazy fixtures / a resolver-gated guard
    - Result: zero collection errors under `v2`; zero reduction in v1 cell count under `v1`/unset;
      the live-TCP cells remain v1-arm cells gated off under v2 by the conftest rule
    - _Requirements: 2.2, 2.3, 2.4_

  - [x] 4.3 Enforce the single application module object across v1-only cells **[test-file]**
    - In `test_openai_sdk_compat_phase2.py` and `test_openai_sdk_compat_live_uvicorn.py`, ensure
      every v1-only cell resolves the gateway through the ONE `_resolved_main()` / resolver
      accessor (single module object), never a bare `import ai_mesh_gateway.main as gm` at module
      top that binds a second identity
    - Confirm `test_openai_sdk_compat.py` keeps its existing lazy-in-fixture v1 import (no change
      needed beyond consistency with the accessor)
    - This removes the dual-module-identity interaction so a whole-directory collect equals the sum
      of single-file collects under every app value
    - _Requirements: 3.1, 3.2, 3.3_

  - [x] 4.4 Verify WAVE 3 collection under v2 **[test-file]**
    - Run `cd gateway_v2 && AMF_CONFORMANCE_APP=v2 .venv/bin/python -m pytest
      tests/openai_conformance/ --co -q` and confirm zero collection errors
    - Collect each file individually and the whole directory, and confirm the per-file-sum equals
      the directory-collect cell set under `AMF_CONFORMANCE_APP=v2` (and under `v1`)
    - _Requirements: 2.1, 2.2, 3.1, 3.2, 3.3_

- [x] 5. WAVE 4 — v2 arm reconcile against the shared helper (test layer)
  - [x] 5.1 Re-point the GW15 chat-surface subset at the shared helper **[test-file]**
    - Modify `gateway_v2/tests/openai_conformance/test_gw15_chat_surface_v2.py`
    - Keep the shipped wiring (`build_app` + `StubProviderClient` +
      `tests.edge._chat_lifecycle_fixtures` + the single `_stock_client` helper +
      `importorskip("openai")`/`("httpx")`)
    - Re-point overlapping inline wire assertions at `wire_contract.py` so the specification is
      single-bodied; add the SDK version-provenance cell asserting installed `openai.__version__`
      is at or above `SDK_INSTALLED_FLOOR`
    - _Requirements: 4.1, 4.2, 4.3, 5.2, 6.2_

  - [x] 5.2 Re-point the GW16 misc-surface subset at the shared helper **[test-file]**
    - Modify `gateway_v2/tests/openai_conformance/test_gw16_misc_surface_v2.py`
    - Keep shipped wiring; re-point overlapping inline wire/error-envelope assertions at
      `wire_contract.py`, drawing the content-filter status/code mapping from the ledger where v2
      deliberately differs
    - _Requirements: 4.2, 5.2, 6.2_

  - [x] 5.3 Re-point the MCP/RAG/vector shapes subset at the shared helper **[test-file]**
    - Modify `gateway_v2/tests/openai_conformance/test_mcp_rag_vector_shapes.py`
    - Keep shipped wiring; re-point overlapping inline wire assertions at `wire_contract.py`
    - _Requirements: 4.2, 5.2_

  - [x] 5.4 Fix genuine local v2 surface gaps exposed by the now-running suite **[product-code]**
    - For any v2 cell that fails under `AMF_CONFORMANCE_APP=v2` and is NOT a registered
      Expected_Diff_Ledger entry, fix the V2_App surface locally so the cell passes against v2
    - For any failing cell that DOES correspond to a §10.2.2 deliberate change, confirm it reads
      the value through `expected_diff(key)` instead (register the entry if missing) rather than
      forcing v2 to match v1
    - _Requirements: 6.3, 6.4_

- [x] 6. WAVE 5 — verification (both arms + directory + Gate_Obligation)
  - [x] 6.1 Verify both arms run green **[test-file]**
    - Run `cd gateway_v2 && AMF_CONFORMANCE_APP=v1 .venv/bin/python -m pytest
      tests/openai_conformance/ -q` with zero failing cells and no reduction in v1 collected/passing
      cells
    - Run `cd gateway_v2 && AMF_CONFORMANCE_APP=v2 .venv/bin/python -m pytest
      tests/openai_conformance/ -q` with zero failing cells
    - Confirm the directory-collect equals the sum-of-single-file collects under both app values
    - _Requirements: 5.1, 5.2, 1.4, 2.4, 3.1, 3.2, 3.3_

  - [x] 6.2 Run the full Gate_Obligation **[product-code]**
    - Run the full offline suite from `gateway_v2/` and confirm no failing tests, passing count no
      lower than the baseline 1878 passed / 95 skipped / 1 xfailed (adjusted upward only by cells
      newly enabled by this spec)
    - Run `cd gateway_v2 && .venv/bin/mypy --strict` over the `gateway_v2` tree — zero errors
    - Run `cd gateway_v2 && .venv/bin/ruff check` over the `gateway_v2` tree — zero violations
    - Run `lint-imports` and confirm the 3 import contracts ("Gateway v2 layers", "Resolve imports
      only domain", "No HTTP framework outside edge") all remain satisfied — no product→test and no
      product→v1 import introduced
    - Run the 7 AST gates and confirm all exit zero
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

  - [x] 6.3 Confirm the deferred CI/cloud scope stays out of this plan **[test-file]**
    - Verify no task in this plan adds a CI Node matrix (second-language client conformance),
      branch protection, required checks, a reproducible-image build, or a GW02 live-v1 parity
      harness; confirm all verification runs only against resources available on `suraj-revamp`
      with no cloud or GPU access
    - _Requirements: 8.1, 8.2, 8.3, 8.4_

## Notes

- This plan is test-harness + configuration plumbing over an existing suite. The design has **no
  Correctness Properties section** (PBT is explicitly not applicable to collection wiring, a
  declarative ledger, and external-SDK conformance), so there are **no property-test sub-tasks**.
- **[product-code]** tasks touch `gateway_v2.contracts` and are subject to `mypy --strict`,
  `lint-imports`, and the 7 AST gates. **[test-file]** tasks edit only files under
  `gateway_v2/tests/`.
- The two arms' internal wiring is kept as shipped; the shared `wire_contract.py` is the single
  body of wire assertions both arms call, and the resolver + ledger are the single app switch.
- Each task references the specific requirement sub-clauses it satisfies for traceability.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2"] },
    { "id": 2, "tasks": ["1.3", "2.1"] },
    { "id": 3, "tasks": ["3.1"] },
    { "id": 4, "tasks": ["4.1", "4.2"] },
    { "id": 5, "tasks": ["4.3"] },
    { "id": 6, "tasks": ["4.4", "5.1", "5.2", "5.3"] },
    { "id": 7, "tasks": ["5.4"] },
    { "id": 8, "tasks": ["6.1"] },
    { "id": 9, "tasks": ["6.2", "6.3"] }
  ]
}
```
