# GW33 Implementation Plan (output parity)

Worktree: `/work/suraj/AI_Mesh_Firewall/.worktrees/gw33` (branch `gw33-output-parity`). Use absolute paths; relative paths land in the parent workspace.
Specs: `.kiro/specs/gw33-output-parity/{requirements,design,tasks}.md`. `tasks.md` is the per-task source of truth (exact files, sub-tasks, review tags H-/M-/L-/D-). This plan fixes the ORDER, the commit grain, and the gates. Do not re-decide the design.

Design status: `design-verdict.json` is APPROVED (revision 5). Findings: L-1 (exfil default-off, `V2_EXFIL_ORGS` empty, D-29) and N-1 (D-2 bullet long). No unresolved HIGH/MEDIUM, so no "Planner resolutions" section was needed in design.md. Carry L-1 into the changelog and final report: L33-1 is live only for orgs listed in `V2_EXFIL_ORGS`.

Decomposition decision: NOT restructured into FEAT steps. The work is a strictly layered chain (each phase imports the previous one), so parallel or separate FEAT coders would only add merge risk. The existing build-loop (writes `review.json`, stops on APPROVED), gate-suite and finalize steps run as is.

Paths: all code under `gateway_v2/gateway_v2/` (package) unless prefixed `control/`, `lint/`, `tests/`. Run gates with cwd `gateway_v2/` (see Gates).

Rules for every phase
- Each phase ends buildable: `pytest` for its new tests plus `mypy --strict` on touched modules plus `lint-imports`. Commit locally per phase (never push).
- Layer order (import-linter): `edge > admit > plan > detect > resolve > dispatch > egress > audit > runtime > contracts > domain`. `resolve` imports only `domain`; `egress` never imports `detect`; scanners are injected from `edge`.
- Fail closed everywhere. No capacity literal outside `runtime/resources.py` and (after P0) `runtime/resources_output.py`.
- Property tests: seeded `random.Random`, no hypothesis, >= 100 iterations (10,000 where cheap), tagged in source `# Feature: gw33-output-parity, Property N: <name>` with a docstring naming the requirement. Properties 1-16 each need a tag and an iteration constant.
- Modules at the 800-line cap (`egress/stream.py`, `runtime/resources.py`) get a behaviour-preserving extraction BEFORE any other edit.
- Wording everywhere: "local half closed; live L33-1..L33-3 deferred". Never claim GW33 exit or a passed live gate.

## P0. Preconditions (tasks 0.1-0.4)
- Apply the "Requirements amendments" table of design.md to `.kiro/specs/gw33-output-parity/requirements.md`.
- `lint/check_capacity_literals.py`: `ALLOWED_REL` -> frozen `ALLOWED_RELS = ("runtime/resources.py", "runtime/resources_output.py")`; planted-violation test (existing gates test suite under `tests/gates/`) proves other modules still fail.
- Stub `detect/output/ruleset.py` with one constant of each planned shape; run the capacity-literal gate; fix by renaming or derivation, never by widening the exemption.
- Verify: `python -m lint.check_capacity_literals gateway_v2/gateway_v2` exit 0; gate tests pass.

## P1. Domain (task 1)
Files: `domain/category.py` (IP_LEAK, HALLUCINATION, CANARY), `domain/__init__.py`, `domain/decision.py` (Disposition.REWRITE, `_rank` ALLOW0 FLAG1 REDACT2 REWRITE3 BLOCK4, `review_required`, `codes`, `combine_decisions`), `domain/plan.py` (`Rule.rewrite_template`, `GroundingMode`, `LEGACY_IP_RULE_PREFIX`, `builtin_output_detector(rule)`, `RuleCondition.allow_hosts`), `domain/posture.py` (codes `edit_overlap_redacted`, `rewrite_template_missing`, `rewrite_unverified`, `final_check_unapplied`, `output_length_exceeded`, `canary_unavailable`).
Tests: `tests/domain/test_gw33_vocabulary.py` (Property 3; defaults keep existing constructions valid; reserved-namespace regex returns None for customer rules, incl. `exfil`).
Verify: `pytest tests/domain -q`; full `pytest -q` shows no new failures (existing exhaustive-enum tests may need REWRITE added).

## P2. Runtime resources (task 2)
Files: new `runtime/resources_output.py` (`OutputBoundsMixin`, `OutputBounds`, `DEFAULT_OUTPUT_BOUNDS`, `REWRITE_TEMPLATE_MAX_CHARS`, `exfil_allow_hosts_max`); `runtime/resources.py` only gets the mixin base (import + class bases). Run `check_sizes` after.
Tests: `tests/runtime/test_gw33_resources.py` (clamp never less sensitive than default across three contract sizes; `CapacityUnavailable` on non-positive).
Verify: pytest on that file; `check_capacity_literals`, `check_sizes` exit 0.

## P3. Egress (task 3)
Order inside the phase: 3.0 extraction of `_publish`/`_note_output`/`_forced_release_remainder_edits` into `egress/stream_publish.py` FIRST (existing egress/e2e suites unchanged and green, `stream.py` <= 740 lines), then 3.6 `egress/output_errors.py`, 3.1 `egress/rewrite_verify.py`, 3.2 `egress/output_guard.py`, 3.3 `egress/holdback_tradeoff.py`, 3.4 `egress/rewrite_model.py`, 3.5 wiring in `egress/stream.py` and `egress/strict.py` (final_detector, delta_detector, typed except arms before generic `except Exception`, StreamStats.review_required).
Tests: `tests/egress/test_gw33_{rewrite_verify,tradeoff,rewrite_model,final_check,delta_detector}.py`; extend `tests/egress/test_lgw12b_stream.py` drift test (canary present; ip_leak/exfil_sink absent). Properties 2, 4 (differential), 8.
Verify: `pytest tests/egress -q`; non-rewrite path byte-identical to before.

## P4. Dispatch (task 4)
Files: `dispatch/verify.py` (+REWRITE in `_REDACTING_DISPOSITIONS`), docstring fixes in `dispatch/authorized.py` and `resolve/authorize.py`, `audit/console_schema.py::_ACTIONS` += "rewrite". Do not touch the `rewrite: bool` bit or any LGW11-4 test.
Tests: `tests/dispatch/test_gw33_input_rewrite.py` (incl. AST test that no `edge/` caller passes `rewrite=True`), authorize() mint test.
Verify: `pytest tests/dispatch tests/contracts -q`; console-inventory gate `python -m lint.check_console_inventory`.

## P5. Resolver (task 5)
Files: `resolve/decision.py`, new `resolve/rewrite.py`, `resolve/review.py`, `resolve/resolver.py`, `resolve/conflict.py::_category_join` (D-32). New `lint/check_single_authority.py` with fixtures `tests/lint/fixtures/single_authority/bad_*.py`.
Tests: `tests/resolve/test_gw33_resolver.py` (Properties 3, 4 with frozen pre-change reference copy, 8), `test_gw33_overlap.py` (Property 15), `tests/lint/test_gw33_single_authority.py` (Property 5).
Verify: `pytest tests/resolve tests/lint -q`; `lint-imports` keeps "Resolve imports only domain".

## P6. Holdback class (task 6)
`detect/holdback.py`: add `canary` (word class, TERMINATE); `_classify_word_suffix` -> "canary" for `cnry_` prefix. No ip_leak/exfil_sink class.
Tests: `tests/detect/test_gw33_holdback.py` (Property 14 differential vs pre-change scan; agrees with egress mirror).
Verify: `pytest tests/detect/test_gw33_holdback.py tests/egress/test_lgw12b_stream.py -q`.

## P7. Output detectors (task 7)
Files in `detect/output/`: `ruleset.py`, `ip_leak.py`, `exfil.py`, `canary.py`, `grounding.py`; `detect/guard/backend.py` gets the optional `HallucinationCapable` protocol. Every detector converts internal exceptions/budget misses to one UNAVAILABLE finding; evidence is None.
Tests: `tests/detect/test_gw33_{ip_leak,exfil,canary,grounding,unavailable}.py` (Properties 6, 7, 9, 13); corpus fixtures under `tests/detection_corpus/` (L33-1/2/3 shapes plus negatives).
Verify: `pytest tests/detect -q`; AST test: no provider import in grounding.

## P8. Plan compiler and document (task 8)
Files: `plan/compiler.py` (REWRITE supported, `rewrite_template`, D-9 refusal keyed on `builtin_output_detector`, legacy prefix exempt, allow_hosts validation, hash only when non-empty), new `plan/rewrite_template.py`, `plan/document.py` (encode/decode, `requires: ["rewrite/1"]` only when needed).
Tests: `tests/plan/test_gw33_compiler.py` (Property 11; content_hash preservation); 8.4a GW26-shaped plans through ALL FOUR `compile_plan` sites (`edge/dev_components.py`, `edge/dev_plan_feed.py`, `plan/delta.py`, `state_control/projector.py`) plus AST enumeration; 8.4b combined-plan install test; 8.5 template check; 8.6 Dockerfile copy-list transitive-import test.
Verify: `pytest tests/plan tests/state_control -q` (adjust to actual dir names found under `gateway_v2/tests`).

## P9. Edge and audit (task 9)
Files: new `edge/canary_inject.py`, `edge/output_detectors.py`, `edge/output_length.py`; edits `edge/audit_emit.py`, `audit/console_schema.py`, `edge/mcp_reply.py`, `edge/errors.py`, `edge/serve.py`, `edge/dev_components.py`. Legacy-IP shim per D-2; canary D-19.
Tests: `tests/edge/test_gw33_{canary_inject,output_detectors,audit,mcp_rewrite}.py` (Properties 8, 9, 10, 16).
Verify: `pytest tests/edge -q`.

## P10. Streaming and offline L33 equivalents (task 10)
`tests/e2e/test_gw33_streaming.py` (Property 1), `test_lgw33_1_exfil_image.py`, `test_lgw33_2_ip_leak.py`, `test_lgw33_3_grounding.py` (docstring "offline equivalent, not the live gate"), bounded-work/wall-clock tests (10.3).
Verify: `pytest tests/e2e -k gw33 -q`.

## P11. Control plane (task 11)
Files under `control/ai_mesh_control/core/`: `firewall_v2_publish.py`, `firewall_v2_map.py`, `firewall_config_serializer.py`; update GW27 ledger and tests asserting "rewrite refused" so they assert the new state (never delete without an equivalent assertion). Settings: `V2_REWRITE_ORGS`, `V2_CANARY_ORGS`, `V2_CANARY_STRICT_ORGS`, `V2_EXFIL_ORGS`, `V2_EXFIL_ACTION`, `V2_EXFIL_ALLOW_HOSTS`, all default empty/safe.
Tests: extend `tests/plan/test_firewall_v2_map.py`, `tests/plan/test_dev_plan_publish.py` (locate real paths with a repo search; they may live under `control/`); new `test_gw33_publish.py` (Property 12; `_UNSUPPORTED_ACTIONS == {"model_downgrade"}`).
Verify: run the control tests with the project's Django/pytest invocation (discover from `control/` config; same container style as the GW30 gate doc).

## P12. Close-out (task 12)
Docs only: `docs/plans/2026-gw33-output-parity.md` + evidence under `docs/plans/evidence/`, one-line pointer in `AGENTS.md`, entry in `docs/pipeline/CHANGELOG.md` if present, republish runbook step (12.3/12.5), follow-up card GW33-F1, L-1 note (exfil default-off). Do not edit unrelated sections of AGENTS.md.

## Gates (offline; cwd `gateway_v2/` unless noted; follows docs/plans/2026-11-02-gw30-offline-verification-gate.md)
1. `pytest -q` (full) and `pytest -q -k "gw33 or lgw33"`; known pre-existing failures (e.g. `tests/gates/test_check_tenant_scale.py::test_the_shipped_data_plane_is_clean`) must not grow; compare against base with `lint/compare_junit.py` if needed.
2. `mypy --strict`
3. `ruff check gateway_v2`
4. `lint-imports` (all contracts kept)
5. AST gates: `python -m lint.check_capacity_literals gateway_v2/gateway_v2`, `check_no_module_mutable`, `check_frozen_dataclasses`, `check_http_outside_edge_resolve`, `check_sizes`, `check_single_authority`, `check_console_inventory`.
6. Property-tag gate: Properties 1-16 each tagged with iteration constant >= 100.
7. Control tests for P11.
No live gates. Clean up any temp files.

## Risks to watch
- `stream.py` and `resources.py` at the 800-line cap: extract first.
- `compile_plan` has four callers: signature must stay unchanged.
- Existing tests asserting `Disposition` has four members or REWRITE->REDACT must be updated with equivalent assertions.
- GW27 is not on this tree: no dependency on its function names (D-21).
