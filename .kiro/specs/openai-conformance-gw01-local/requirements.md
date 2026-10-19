# Requirements Document

## Introduction

This spec covers the **local half of card GW01** in the v3 backend rewrite (runbook `docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md`, §10.2.1, §10.2.2, §10.8). GW01's charter is: "Freeze the OpenAI wire contract as an implementation-agnostic conformance suite in CI." The v1 package shipped a 77-test, ~1,979-line OpenAI SDK conformance suite frozen against `openai==2.38.0`; that suite "IS the specification of gateway-v2's public surface."

The suite is **already ported** into `gateway_v2/tests/openai_conformance/` and an env-selected app resolver already exists at `gateway_v2/gateway_v2/contracts/openai_conformance/harness.py` (`conformance_app_name()` reads `AMF_CONFORMANCE_APP`, defaulting to `v1`; `tcp_base_url()` reads `AMF_CONFORMANCE_BASE_URL`; both fail closed on an unknown app). The remaining work is to make the ported suite actually **run and pass against the v2 app**, which it has never done.

Four verified defects block this (all confirmed on disk, branch `suraj-revamp`):

1. **Stale skip.** `gateway_v2/tests/openai_conformance/conftest.py` skips every cell carrying the `sdk_app` fixture when `AMF_CONFORMANCE_APP=v2`, with reason `"v2 has no OpenAI surface yet (GW15)"`. GW15/GW16/GW17/GW18 have all shipped the v2 surface, so this skip is obsolete and has prevented the suite from ever running green against v2.
2. **Collection errors under v2.** `test_openai_sdk_compat_phase2.py` imports `ai_mesh_gateway.responses_adapters` and `ai_mesh_gateway.main` at module top level; `test_openai_sdk_compat_live_uvicorn.py` imports `ai_mesh_gateway.main` and `ai_mesh_gateway.*` at module top level. These are v1 package names, so under v2 both files raise collection errors ("no tests collected, 1 error").
3. **Directory-run suppression.** Running the whole directory under v2 collects only 36 cells (33 pass / 3 skip), but `test_openai_sdk_compat.py` alone collects 48 cells — a directory-level collection interaction suppresses the large files.
4. **Version drift.** The suite is frozen against `openai==2.38.0`; the venv has `openai 3.29.0` installed (a major version jump). The frozen contract is the HTTP **wire** contract, not the SDK's Python API, and the suite must still prove the stock installed SDK works.

This spec defines WHAT must be true for the ported suite to run green against both apps. It does not prescribe implementation.

## Glossary

- **GW01**: The runbook card whose local half this spec covers — freeze the OpenAI wire contract as an implementation-agnostic conformance suite.
- **Conformance_Suite**: The ported test suite in `gateway_v2/tests/openai_conformance/` (`test_openai_sdk_compat.py` plus the `phase2`, `phase2_adversarial`, `phase5_correlation`, `phase6_guards`, `live_uvicorn` siblings, the GW15/GW16 subsets, `node/`, `stub_server.py`, and the live-TCP cells).
- **App_Resolver**: `gateway_v2/gateway_v2/contracts/openai_conformance/harness.py`, which selects the application under test from `AMF_CONFORMANCE_APP`.
- **V1_App**: The legacy gateway application (`ai_mesh_gateway.*`), selected when `AMF_CONFORMANCE_APP` is unset or `v1`.
- **V2_App**: The rewritten gateway application (`gateway_v2.*`), selected when `AMF_CONFORMANCE_APP=v2`.
- **Wire_Contract**: The HTTP/SSE behaviour the stock `openai` SDK depends on (typed JSON shapes, streaming chunk framing, `data: [DONE]` termination, error-envelope `message`/`type`/`code`/`param`/`request_id`, status codes). This is the frozen contract — distinct from any single pinned version of the SDK's Python API.
- **Stock_SDK**: The unmodified `openai` python package as installed in `gateway_v2/.venv` (currently `3.29.0`), consumed with only a base-URL and API-key swap.
- **Expected_Diff_Ledger**: A pre-registered record of the deliberate v2-vs-v1 behaviour changes enumerated in runbook §10.2.2, each of which is encoded as an expected difference rather than treated as a conformance failure.
- **Gate_Obligation**: The standard local verification gate — full offline suite + `mypy --strict` + `ruff` + `lint-imports` (3 import contracts) + the 7 AST gates — all green, with baseline 1878 passed / 95 skipped / 1 xfailed.
- **Conformance_Cell**: A single collected pytest test item within the Conformance_Suite.

## Requirements

### Requirement 1: Remove the stale "no v2 surface" skip

**User Story:** As a backend engineer freezing the OpenAI wire contract, I want the obsolete v2 skip removed, so that the ported conformance suite actually executes against the v2 app instead of being silently skipped.

#### Acceptance Criteria

1. WHEN the Conformance_Suite is collected WHILE `AMF_CONFORMANCE_APP` equals `v2`, THE Conformance_Suite SHALL execute every Conformance_Cell that carries the `sdk_app` fixture rather than marking it skipped.
2. THE `gateway_v2/tests/openai_conformance/conftest.py` module SHALL contain no skip marker whose reason references the absence of a v2 OpenAI surface.
3. WHERE `AMF_CONFORMANCE_BASE_URL` is set (TCP mode), THE Conformance_Suite SHALL continue to skip the in-process ASGI cells (`sdk_app` / `sdk_client` fixtures) as a separate job, preserving the existing TCP-vs-ASGI separation.
4. WHEN the Conformance_Suite is collected WHILE `AMF_CONFORMANCE_APP` is unset or equals `v1`, THE Conformance_Suite SHALL continue to execute against the V1_App with no change in its collected or passing cells.

### Requirement 2: Fix the v2 collection errors

**User Story:** As a backend engineer, I want the phase2 and live-uvicorn files to collect cleanly under v2, so that no ported cell is lost to an import-time collection error.

#### Acceptance Criteria

1. WHEN `test_openai_sdk_compat_phase2.py` is collected WHILE `AMF_CONFORMANCE_APP` equals `v2`, THE Conformance_Suite SHALL collect its cells with zero collection errors.
2. WHEN `test_openai_sdk_compat_live_uvicorn.py` is collected WHILE `AMF_CONFORMANCE_APP` equals `v2`, THE Conformance_Suite SHALL collect its cells with zero collection errors.
3. IF a Conformance_Cell depends on a module that exists only in the V1_App, THEN THE Conformance_Suite SHALL resolve that dependency through the App_Resolver so that the cell targets the application selected by `AMF_CONFORMANCE_APP` rather than a hard-coded `ai_mesh_gateway` import.
4. WHEN `test_openai_sdk_compat_phase2.py` and `test_openai_sdk_compat_live_uvicorn.py` are collected WHILE `AMF_CONFORMANCE_APP` is unset or equals `v1`, THE Conformance_Suite SHALL collect their cells with zero collection errors and no reduction in their v1 cell count.

### Requirement 3: Resolve the directory-run collection suppression

**User Story:** As a backend engineer, I want a whole-directory run to collect the same cells a single-file run collects, so that no large file is silently dropped when the suite runs as a directory.

#### Acceptance Criteria

1. WHEN the directory `gateway_v2/tests/openai_conformance/` is collected WHILE `AMF_CONFORMANCE_APP` equals `v2`, THE Conformance_Suite SHALL collect every Conformance_Cell that `test_openai_sdk_compat.py` collects when run as a single file.
2. WHEN the directory `gateway_v2/tests/openai_conformance/` is collected under any supported value of `AMF_CONFORMANCE_APP`, THE Conformance_Suite SHALL collect the same set of Conformance_Cells that the sum of its individual single-file runs collects.
3. IF two Conformance_Suite files import the gateway application under distinct module identities, THEN THE Conformance_Suite SHALL resolve them to a single application module object so that a directory run does not re-execute the gateway into a second module object.

### Requirement 4: Reconcile the openai 2.38 to 3.29 version drift

**User Story:** As a backend engineer, I want the frozen wire contract validated against the installed SDK major version, so that the contract proves the stock SDK a real caller would install still works.

#### Acceptance Criteria

1. WHEN the Conformance_Suite runs against either the V1_App or the V2_App, THE Conformance_Suite SHALL exercise the Wire_Contract using the Stock_SDK as installed in `gateway_v2/.venv` with only a base-URL and API-key swap.
2. THE Conformance_Suite SHALL assert the Wire_Contract (typed JSON shapes, streaming chunk framing, `data: [DONE]` termination, error-envelope fields, and status codes) rather than any version-specific internal of the SDK's Python API.
3. IF the installed SDK major version renames or relocates a Python API symbol the suite references, THEN THE Conformance_Suite SHALL adapt its reference so that the same Wire_Contract assertion holds against the installed SDK.
4. THE Conformance_Suite SHALL record the SDK version against which the Wire_Contract is frozen, so that a future version jump is a deliberate, reviewable change rather than a silent drift.

### Requirement 5: Implementation-agnostic — green against both v1 and v2

**User Story:** As a backend engineer, I want the identical suite green against both apps, so that the conformance suite is a genuine implementation-agnostic specification and not a v1-only test.

#### Acceptance Criteria

1. WHEN the Conformance_Suite runs WHILE `AMF_CONFORMANCE_APP` equals `v1` or is unset, THE Conformance_Suite SHALL complete with zero failing Conformance_Cells.
2. WHEN the Conformance_Suite runs WHILE `AMF_CONFORMANCE_APP` equals `v2`, THE Conformance_Suite SHALL complete with zero failing Conformance_Cells.
3. THE Conformance_Suite SHALL assert the Wire_Contract through the App_Resolver without naming either the V1_App or the V2_App package directly in the shared assertion paths.
4. WHEN the App_Resolver receives an `AMF_CONFORMANCE_APP` value outside the allowed set, THE App_Resolver SHALL fail closed with an error naming the rejected value.

### Requirement 6: Encode deliberate v2-vs-v1 behaviour changes as expected diffs

**User Story:** As a backend engineer, I want the deliberate v2 behaviour changes recorded as expected differences, so that the suite does not force v2 to match v1 where the runbook mandates a different behaviour.

#### Acceptance Criteria

1. THE Expected_Diff_Ledger SHALL record each deliberate v2-vs-v1 behaviour change enumerated in runbook §10.2.2, including: injection/jailbreak detection measured against the corpus with published recall/FPR; benign inline code emitting a SIGNAL rather than a terminal block; a degraded scan producing an explicit UNAVAILABLE finding status with a signed failure posture; org-policy precedence by signed rule priority rather than severity max; the unset tri-state resolving to one plan identical on every surface; missing tenant config producing an explicit PLAN_UNAVAILABLE; and a streaming block withholding before the first byte or using an explicitly selected strict mode rather than truncating after a partial emission.
2. WHEN a Conformance_Cell observes a v2 behaviour that corresponds to an Expected_Diff_Ledger entry, THE Conformance_Suite SHALL treat that observation as expected rather than as a failing cell.
3. IF the now-running suite exposes a genuine v2 public-surface gap that is NOT an Expected_Diff_Ledger entry, THEN THE Conformance_Suite SHALL cause the corresponding Conformance_Cell to fail WHILE `AMF_CONFORMANCE_APP` equals `v2` until the gap is fixed locally or registered as a new ledger entry.
4. WHERE a genuine v2 gap exposed by the suite is small and local in scope, THE resolution SHALL fix the V2_App surface so that the Conformance_Cell passes against v2.

### Requirement 7: Standard local gate obligation stays green

**User Story:** As a backend engineer, I want the standard local gate to stay green after these changes, so that the conformance work does not regress the rest of the gateway-v2 package.

#### Acceptance Criteria

1. WHEN the Gate_Obligation is run after the GW01 local changes, THE full offline test suite SHALL complete with no failing tests and a passing count no lower than the baseline 1878 passed / 95 skipped / 1 xfailed, adjusted upward only by cells newly enabled by this spec.
2. WHEN `mypy --strict` is run over the `gateway_v2` tree after the changes, THE type check SHALL complete with zero errors.
3. WHEN `ruff` is run over the `gateway_v2` tree after the changes, THE linter SHALL report zero violations.
4. WHEN `lint-imports` is run after the changes, THE 3 import contracts SHALL all remain satisfied.
5. WHEN the 7 AST gates are run after the changes, THE AST gates SHALL all exit zero.

### Requirement 8: Deferred CI and cloud work are explicit non-goals

**User Story:** As a backend engineer, I want the deferred CI/cloud scope stated explicitly, so that reviewers do not mistake the local half for the whole card.

#### Acceptance Criteria

1. THE requirements SHALL declare the CI Node matrix (second-language client conformance) as a non-goal of the local half, deferred because it requires the CI runner.
2. THE requirements SHALL declare branch protection, required checks, and the reproducible-image build as non-goals of the local half, deferred to the human/cloud owner.
3. THE requirements SHALL declare the GW02 parity harness against the live V1_App as a non-goal of the local half.
4. WHILE the deferred items remain out of scope, THE local verification SHALL depend only on resources available on `suraj-revamp` with no cloud or GPU access.
