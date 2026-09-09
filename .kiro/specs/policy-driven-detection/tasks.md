# Implementation Plan: Policy-Driven Detection (no default rules)

## Overview

Convert the design into incremental, test-driven coding steps for a code-generation LLM. Each step
builds on the previous and ends by wiring things together; no orphaned code. Focus ONLY on writing,
modifying, or testing code.

This is a **production behaviour change** across the gateway (`gateway/ai_mesh_gateway/`), the control
plane (`control/ai_mesh_control/`), and the frontend, unifying on: **Tier-1 = enabled policies only,
Tier-2 = opt-in model only, zero mandatory detection, all surfaces, clean cutover.** Language is
Python (gateway + control) and the existing frontend stack (React) for the toggle + E2E leg.

Because the repo mandates the four-memory changelog protocol for pipeline/MCP changes, every
pipeline-touching change records a `PIPELINE-xxxx` entry and every MCP-surface change a `CHG-xxxx`
(Ruflo memory + `AGENTS.md` pointer + Cursor mirror + canonical `docs/pipeline/CHANGELOG.md` /
`docs/mcp/HARDENING_CHANGELOG.md`), staged narrowly, in the same commit.

Build order (design §Components): (1) enforcement-seam + config defaults so a zero-policy org is
passthrough on the chat path; (2) delete the built-in Tier-1 default scan and drive Tier-1 from the
policy engine; (3) Tier-2 opt-in gating (chat + OpenAI SDK); (4) re-home the built-in families as
seeded default-OFF policy packages (control plane); (5) remove the legacy scan toggles; (6) extend to
every other surface (streaming, RAG, embeddings, MCP); (7) frontend Tier-2 toggle; (8) unit/property
tests + gateway gate; (9) the MANDATORY Docker 100+ prompt live E2E harness (frontend + OpenAI SDK);
(10) cutover wiring, changelog, and final gate.

Gates: gateway unit gate `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests -q`;
control gate per repo convention; live E2E via Docker Compose (Requirement 9).

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2.1"] },
    { "id": 2, "tasks": ["2.2", "2.3"] },
    { "id": 3, "tasks": ["3.1"] },
    { "id": 4, "tasks": ["3.2"] },
    { "id": 5, "tasks": ["3.3", "3.4", "3.5", "4.1", "6.1"] },
    { "id": 6, "tasks": ["4.2", "4.3", "4.4", "5.1", "6.2", "6.3"] },
    { "id": 7, "tasks": ["5.2", "8.1"] },
    { "id": 8, "tasks": ["5.3", "5.4", "5.5", "8.2", "7.1", "7.2", "7.3", "7.4"] },
    { "id": 9, "tasks": ["7.5"] },
    { "id": 10, "tasks": ["9"] },
    { "id": 11, "tasks": ["10.1", "10.2"] },
    { "id": 12, "tasks": ["10.3", "10.4", "10.5", "11"] },
    { "id": 13, "tasks": ["12"] }
  ]
}
```

Reading of the waves: task 1 (baseline scaffold) first; the chat enforcement seam (2.x) then the
built-in-scan removal (3.x) are the critical spine. Once 3 lands, Tier-2 gating (4.x), the package
seeder (5.x), and the legacy-toggle removal (6.x) parallelize; the per-surface extension (7.1–7.4)
fans out after 3/4/6; the frontend toggle (8.x) is parallel to the backend. Task 9 (gateway unit
gate) gates the whole unit layer; the MANDATORY Docker live E2E harness (10.x) depends on the
packages (5), all surfaces (7), the frontend toggle (8), and the unit checkpoint (9); cutover (11)
and the final live checkpoint (12) come last. Critical path to completion:
1 → 2 → 3 → 4 → 7 → 9 → 10 → 12.

## Tasks

- [x] 1. Baseline capture and test scaffold (no behaviour change yet)
  - Add a gateway test module `gateway/ai_mesh_gateway/tests/test_policy_driven_detection.py` with a
    reusable fixture that builds an org config dict and an empty/seeded compiled-policy set, plus a
    helper to run the input-enforcement path and assert the resolved `PipelineDecision`
  - Record (in the test module docstring) the CURRENT behaviour for a zero-policy org on an injection
    prompt (blocks today) so the cutover delta is explicit
  - Do NOT change production behaviour in this task
  - _Requirements: 8.1, 8.5_

- [x] 2. Make the enforcement seam policy-only on the chat input path
  - [x] 2.1 Stop passing the built-in scanner verdict into `resolve_and_enforce` on the chat path
    - In `main.py` `proxy_chat`, change the input-enforcement call so `scanner_action` /
      `scanner_recommendation` / `scanner_threat_type` / `scanner_confidence` / `scanner_tier` /
      `scanner_matched_patterns` are None (no built-in guard recommendation); keep passing
      `org_policy_action` + `matched_rules` + `matched_policy_names` from `_policy_check_cached`
    - Ensure a zero-policy org (no matched rules) + Tier-2 off yields `PipelineDecision(action="allow")`
    - _Requirements: 1.1, 1.2, 2.1, 2.4_

  - [x] 2.2 Write property test for zero-policy passthrough (chat input)
    - **Feature: policy-driven-detection, Property 1: For any input content, if the organization's enabled policy set is empty AND `tier2_enabled` is off, then the resolved `PipelineDecision.action` is `allow`, no redaction is applied, and no flag is raised, on every Detection_Surface.**
    - **Validates: Requirements 1.2, 6.2**
    - Assert over a representative attack/PII/secret prompt set that the chat input path resolves
      `allow` with no redaction when no policy is enabled and Tier-2 is off

  - [x] 2.3 Write property test for no built-in contribution
    - **Feature: policy-driven-detection, Property 2: For any input, the enforcement authority receives no built-in scanner recommendation (`scanner_action` / `scanner_recommendation` is None); every non-allow decision traces to a matched enabled policy Rule or an enabled Tier-2 model verdict.**
    - **Validates: Requirements 1.1, 1.4, 2.1**
    - Assert the enforcement call is made with scanner_* None and that a non-allow decision requires a
      matched policy rule (or a Tier-2 verdict)

- [x] 3. Delete the built-in Tier-1 default scan; retain the engine as a policy executor
  - [x] 3.1 Remove the built-in `ATTACK_PATTERNS` default category loop from the scanner
    - In `scanner.py`, remove the automatic `ATTACK_PATTERNS` iteration in `_scan_prompt_sync` /
      `scan_prompt` so the scanner no longer emits an independent injection/command/etc. verdict;
      remove the built-in default PII/secret pattern set as auto-run content
    - Keep `InputScanner`, `compile_pattern`, `redact_all`, and the deobfuscation/unicode machinery
      intact so a policy-driven rule can still be executed and redaction still works
    - _Requirements: 1.1, 2.1, 2.3_

  - [x] 3.2 Route Tier-1 matching through the policy engine only
    - Ensure the only Tier-1 detection source on the chat path is `policy_engine.evaluate(prompt, "",
      enabled_compiled_policies)`; a regex/keyword Rule from an enabled package matches and its
      per-rule action becomes the recommendation
    - Confirm `redact_all` / redaction hints are applied only when an enabled Rule's action is redact
    - _Requirements: 2.1, 2.2, 2.6_

  - [x] 3.3 Write property test for only-enabled-rules-fire
    - **Feature: policy-driven-detection, Property 3: For any input and any set of packages, a Rule produces a verdict if and only if it belongs to a package that is enabled for that organization; a Rule in a disabled or non-enabled package never contributes an action.**
    - **Validates: Requirements 1.3, 4.3, 4.4**

  - [x] 3.4 Write property test for user-selected action is honored
    - **Feature: policy-driven-detection, Property 5: For any matched enabled Rule, the resolved action (before org `enforcement_mode` downgrade) equals the highest-severity user-selected action among matching rules per the lattice `allow < monitor/flag < redact < block`; no hardcoded action is imposed.**
    - **Validates: Requirements 2.2, 4.5**

  - [x] 3.5 Write unit tests for scanner-engine retention and malformed-rule handling
    - A user-authored regex Rule still executes via the retained engine; a malformed Rule is excluded
      with a recorded error and NO fallback to a built-in default
    - _Requirements: 2.3, 2.6_

- [x] 4. Tier-2 opt-in gating on the chat + OpenAI-SDK path
  - [x] 4.1 Resolve `tier2_enabled` to effective-OFF by default and gate the Tier-2 scan
    - In `config.py` / `config_sync.py`, make the resolved `tier2_enabled` default OFF when the value
      is `None`/absent (tri-state preserved; absent ⇒ off)
    - In `main.py` `proxy_chat`, gate the `scan_prompt_with_tier2` invocation so Tier-2 runs ONLY when
      the resolved `tier2_enabled` is true; when off, Tier-2 contributes no verdict
    - Keep Tier-2 model-only (no policy feeds it); its action feeds `resolve_and_enforce`
    - _Requirements: 3.1, 3.2, 3.4, 3.5, 3.7_

  - [x] 4.2 Write property test for Tier-2 opt-in gating
    - **Feature: policy-driven-detection, Property 6: For any request, the Tier-2 model scan executes if and only if the resolved `tier2_enabled` is true; when it does not execute it contributes no verdict; an unresolved or absent value resolves to not-executing.**
    - **Validates: Requirements 3.2, 3.7**

  - [x] 4.3 Write property test for Tier-2/Tier-1 separation
    - **Feature: policy-driven-detection, Property 8: For any request, no policy or Rule influences a Tier-2 decision, no Tier-2 verdict is produced when `tier2_enabled` is off, and no model influences a Tier-1 decision.**
    - **Validates: Requirements 2.5, 3.4, 3.5**

  - [x] 4.4 Write test for OpenAI-SDK surface Tier-2 parity
    - **Feature: policy-driven-detection, Property 7: For any identical content and identical `tier2_enabled`, the native surface and the OpenAI_SDK_Surface execute (or skip) Tier-2 identically and reach the same decision.**
    - **Validates: Requirements 3.3, 6.4**
    - Drive `/v1/chat/completions` as both the native call and via the shared handler; assert Tier-2
      off ⇒ no `scan_prompt_with_tier2` on either, on ⇒ identical model-decided action

- [x] 5. Re-home the built-in families as seeded, default-OFF policy packages (control plane)
  - [x] 5.1 Add the built-in-family catalog module
    - Add `control/ai_mesh_control/policy/builtin_packs_catalog.py` (modeled on
      `ciso_policy_catalog.py`) enumerating each family (prompt_injection, jailbreak,
      command_injection, sql_injection, data_leakage, path_traversal, goal_hijacking, tool_overreach,
      vector_injection) and a PII/secret family, each rule = regex/keyword + a default user-selectable
      action; command_injection uses the narrowed backtick pattern folded from the parked G0.3 spec
    - _Requirements: 4.1, 4.5_

  - [x] 5.2 Add the idempotent, default-OFF seeder
    - Add `control/ai_mesh_control/policy/builtin_packs_seed.py` + a management command (modeled on
      `ciso_seed.py` / `seed_ciso_policy_package`) that seeds each family as a system `Policy`
      (`is_system=True`, per-org `code`) with `enabled=False`, containing its `Rule`s; idempotent
    - _Requirements: 4.1, 4.2_

  - [x] 5.3 Write unit tests for seeding and default-OFF
    - Seeding creates the packages disabled; re-seeding is idempotent; a seeded-but-disabled package
      contributes no rules to the compiled bundle
    - _Requirements: 4.2, 4.4_

  - [x] 5.4 Write test for enable/disable a package end to end (control → compiled bundle)
    - Enabling a package makes its rules appear in the org's compiled policy bundle with the selected
      action; disabling removes them; per-org isolation holds
    - _Requirements: 4.3, 4.4, 4.6_

  - [x] 5.5 Write property test for per-package isolation
    - **Feature: policy-driven-detection, Property 4: For any input that would only match package B, enabling package A yields passthrough; the decision depends only on the packages enabled for that organization, not on which packages exist or are enabled for other organizations.**
    - **Validates: Requirements 4.6, 2.4**

- [x] 6. Remove the legacy scan toggles as detection drivers
  - [x] 6.1 Remove the toggles from gateway config and gates
    - Remove `input_scan_enabled`, `output_scan_enabled`, `scan_block_on_injection`,
      `scan_block_on_pii` (and RAG/embeddings equivalents) as detection drivers from `config.py`,
      `config_sync._BOOL_KEYS`, and the `main.py` gates (`_input_scan_will_run` and siblings), gating
      Tier-1 solely on the enabled policy set
    - Retain `firewall_enabled` ONLY as a suppression-only master bypass (it may never CAUSE detection)
    - A stale/legacy value for a removed key is ignored for enabling detection (not an error)
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [x] 6.2 Remove the toggles from the control-plane config serialization + frontend
    - Remove the removed keys from the control-plane firewall config surface and the frontend settings
      UI so they no longer imply default detection
    - _Requirements: 5.3_

  - [x] 6.3 Write property test for legacy-toggle inertness
    - **Feature: policy-driven-detection, Property 9: For any configuration value of the removed `input_scan_enabled` / `output_scan_enabled` / `scan_block_on_injection` / `scan_block_on_pii` keys, the detection decision is unchanged; those keys never cause detection.**
    - **Validates: Requirements 5.2, 5.4**

- [x] 7. Extend the policy-driven model to every non-chat surface
  - [x] 7.1 Streaming output guard
    - Make `SecureStreamingResponse` / the output-guard path run only enabled output policies; no
      default output patterns; Tier-2 output gated by `tier2_enabled`
    - _Requirements: 6.1, 6.3_

  - [x] 7.2 RAG (ingest + query)
    - Make `/v1/rag` ingest and query run Tier-1 from enabled policies only; `rag_tier2_enabled`
      effective-default OFF
    - _Requirements: 6.1, 6.3_

  - [x] 7.3 Embeddings
    - Make `_scan_redact_embedding_inputs` / `_scan_redact_metadata` redact only when an enabled policy
      targets it (remove the default-on `input_scan_enabled` gate)
    - _Requirements: 6.1, 6.3_

  - [x] 7.4 MCP tool calls
    - Make the MCP scan orchestrator detect from enabled policies only; `mcp_tier2_enabled` and
      `mcp_redact_result_on_detect` no longer force default detection (effective-default OFF)
    - Record the MCP-surface change under the `CHG-xxxx` changelog protocol
    - _Requirements: 6.1, 6.3_

  - [x] 7.5 Write property tests for fail-toward-no-detection and surface coherence
    - **Feature: policy-driven-detection, Property 10: For any request where the enabled-policy set cannot be resolved, the surface is passthrough; where `tier2_enabled` cannot be resolved, Tier-2 does not run. No unresolved-state path produces a block, redact, or flag.**
    - **Validates: Requirements 6.5, 3.7, 1.4**
    - **Feature: policy-driven-detection, Property 11: For any identical content, enabled policy set, and `tier2_enabled`, the detection decision is the same across chat, OpenAI SDK, RAG, embeddings, MCP, and streaming output, modulo surface-specific content shape.**
    - **Validates: Requirements 6.4**

- [x] 8. Frontend Tier-2 enable/disable control
  - [x] 8.1 Add the Tier-2 toggle to the firewall settings UI
    - Add an explicit enable/disable control bound to `tier2_enabled` that reflects the backend value
      and, on change, updates the control-plane config (which syncs to the gateway)
    - _Requirements: 3.1, 3.6_

  - [x] 8.2 Write a frontend test for the toggle reflecting + changing backend state
    - Toggling in the UI PUTs the setting and the displayed state matches the backend value
    - _Requirements: 3.6_

- [-] 9. Checkpoint — gateway unit gate + zero-policy passthrough proven in unit layer
  - Run `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests -q`; update only the
    pre-existing tests that asserted the now-removed built-in default behaviour
  - Confirm the retained Scanning_Engine, policy engine, and Enforcement_Authority tests still pass
  - Confirm no test proves a built-in default still fires in the zero-policy state on any surface
  - _Requirements: 8.5, 8.6_

- [ ] 10. Build the mandatory Docker live end-to-end verification harness (Requirement 9)
  - [~] 10.1 Author the ≥100-prompt corpus with per-config intended outcomes
    - Create a committed prompt corpus (may reuse/extend the G0.1 detection corpus) of at least 100
      distinct prompts spanning every re-homed family + benign prompts, each annotated with its
      intended outcome per policy configuration (all-off, and per-package-on)
    - _Requirements: 9.2_

  - [~] 10.2 Author the harness that starts Docker services and drives both surfaces
    - Script that: brings up the full stack via Docker Compose and waits for every healthcheck; seeds
      + enables/disables packages and the Tier-2 toggle per run; drives the corpus through BOTH the
      frontend surface (Playwright) and the OpenAI SDK (`openai` client to `/v1/chat/completions`);
      collects per-prompt (surface, config, observed decision, intended, pass/fail)
    - _Requirements: 9.1, 9.6_

  - [~] 10.3 Implement the run matrix and pass/fail gate
    - Config A (all packages OFF, Tier-2 OFF): assert every prompt is passthrough on the live stack
    - Config B(i) (exactly one package ON, repeated per package): assert only that package's rules
      fire with the selected action; other/disabled packages' prompts pass through
    - Both surfaces must agree; any unhealthy service or contradicting decision fails the run
    - _Requirements: 9.3, 9.4, 9.5, 9.6, 9.8, 9.9_

  - [~] 10.4 Emit the reproducible evidence artefact
    - Write the per-prompt results + the exact reproduce commands (Docker up, seed/enable, drive,
      collect) to a committed artefact under a permitted path (e.g. `docs/perf/`)
    - _Requirements: 9.7, 9.10, 8.7_

  - [~] 10.5 Write the reproducibility property test
    - **Feature: policy-driven-detection, Property 12: For any fixed input, fixed enabled policy set, and fixed `tier2_enabled=off`, Tier-1 evaluation is deterministic and repeatable (identical decision across runs), and the live end-to-end run reproduces the same per-prompt outcomes from the recorded commands.**
    - **Validates: Requirements 9.8, 9.10**

- [~] 11. Cutover wiring and documentation
  - Ensure the shipped defaults produce the Zero_Policy_State for every org at cutover (no built-in
    default path remains); no auto-enable of packages on any org's behalf
  - Document (in the evidence artefact / changelog) that an org is unprotected until it enables a
    package or Tier-2, and record the operator runbook to enable packages for the live org
  - Record the pipeline change under the `PIPELINE-xxxx` four-memory changelog protocol (and MCP
    changes under `CHG-xxxx`)
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 8.7_

- [~] 12. Final checkpoint — full gates green and mandatory live E2E passed
  - Gateway unit gate green; control gate green; frontend build/test green
  - Run the Docker live E2E harness (task 10) end to end on the actual services with the 100+ prompt
    corpus through the frontend AND the OpenAI SDK; confirm all-off ⇒ zero detections and
    one-package-on ⇒ only that package's rules fire, on both surfaces
  - Confirm the committed evidence artefact records all results and the reproduce commands, and that
    every prompt matched its intended outcome (any mismatch blocks completion)
  - _Requirements: 8.4, 8.6, 9.3, 9.4, 9.5, 9.6, 9.8, 9.9, 9.10_

## Notes

- **Production behaviour change.** Unlike the additive posture-scoring spec, this modifies live
  gateway, control-plane, and frontend code and changes detection verdicts for every org. Each
  pipeline-touching change MUST record a `PIPELINE-xxxx` four-memory changelog entry and each
  MCP-surface change a `CHG-xxxx` entry (Ruflo memory + `AGENTS.md` + Cursor mirror + canonical
  changelog), staged narrowly in the same commit.
- **Deliberate security-posture inversions (call out in review):** (1) zero policies ⇒ passthrough
  (an org is unprotected until it enables a package or Tier-2); (2) fail-toward-no-detection when the
  policy cache / config cannot be resolved (inverts today's `policy_cache_require_loaded`
  fail-closed). Both are intended per the locked decisions; keep them explicit in the evidence
  artefact and cutover docs.
- **Clean cutover** opens the live `zeroshield` org to passthrough until its packages are explicitly
  enabled — task 11 records the operator runbook; no auto-enable is performed.
- **G0.3 folded in.** The parked `command-injection-fp-fix` narrowed backtick pattern becomes the
  default rule of the re-homed `command_injection` package (task 5.1); do not implement G0.3
  separately.
- **Requirement 9 is the completion gate.** The feature is NOT complete on unit tests alone — the
  Docker-based live E2E run (tasks 10 + 12), driving 100+ prompts through BOTH the frontend and the
  OpenAI SDK, with all-off ⇒ zero detections and one-package-on ⇒ only that package's rules firing,
  must pass and be recorded in a reproducible evidence artefact.
- **Property tests** follow the repo convention (tagged `**Feature: policy-driven-detection,
  Property N: ...**` + `**Validates: Requirements ...**`); the 12 properties are defined in
  `design.md` §Correctness Properties.
- **No new gateway data model.** Reuse `PipelineDecision`, `EvaluationResult`, the config dict, and
  the existing `Policy` / `Rule` control-plane models; the seeder differs from `ciso_seed` only in
  seeding `enabled=False`.
