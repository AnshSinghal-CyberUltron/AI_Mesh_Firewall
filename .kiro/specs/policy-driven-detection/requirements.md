# Requirements Document

## Introduction

This feature makes the AI Mesh Firewall's detection **fully policy-driven with no default rules**.
Today the gateway (`gateway/ai_mesh_gateway/scanner.py`) ships a hardcoded Tier-1 detection library
(`ATTACK_PATTERNS` for injection/jailbreak/command/sql/etc. plus built-in PII/secret patterns) that
runs on every prompt by default, independent of whether an organization has authored any policy. A
brand-new org is fully scanned and can be hard-blocked at confidence 1.00 by rules it never chose.

The target model, decided in `brainstorm.md`, inverts this:

- **Tier-1 = policy only.** Tier-1 is the set of **static, deterministic scanners** (regex/keyword
  matching). It runs **only** what a user's enabled policies specify. There are **no built-in
  default Tier-1 rules**. With no policies enabled, Tier-1 detects nothing and blocks/redacts/flags
  nothing.
- **Tier-2 = model only.** Tier-2 is the semantic model scan (Bedrock/guard model). It is
  **opt-in per organization** via an explicit toggle in the control plane / frontend AND honored by
  the gateway backend AND the OpenAI-SDK-compatible surface. Tier-2 takes **no policies** — when
  enabled, the **model alone** decides the action (allow / block / redact / flag). When disabled
  (the default), Tier-2 does not run anywhere and contributes nothing.
- **Zero mandatory detection.** There is no always-on safety floor. Nothing is ever
  blocked/redacted/flagged unless a user's enabled Tier-1 policy or opt-in Tier-2 model says so.
- **The curated built-in library becomes seeded, toggleable policy packages.** Rather than a
  hardcoded default scanner, the previously-built-in detection families are seeded as **policy
  packages** an organization can turn ON or OFF per org (the same mechanism as the existing CISO
  package, `control/ai_mesh_control/policy/ciso_seed.py`). They are **OFF by default**. When an org
  enables a package, its rules run at Tier-1 with the **action the user selects per rule/family**
  (block / redact / flag / monitor).
- **User picks the action per family/rule.** Every enabled Tier-1 rule carries a user-selected
  action; the firewall never imposes a hardcoded action.
- **Clean cutover, all surfaces.** The change applies to ALL detection entry points at once — chat
  proxy, RAG, embeddings, MCP tool calls, and the streaming output guard — with no grandfathering.

This supersedes the parked `command-injection-fp-fix` (G0.3) spec: the false positive it fixed
disappears because `command_injection` is no longer a default rule.

## Glossary

- **Firewall**: The AI Mesh Firewall data-plane gateway (`gateway/ai_mesh_gateway/`) plus its
  control-plane configuration (`control/ai_mesh_control/`) and frontend.
- **Detection**: Any act of matching prompt or response content and emitting a verdict that can
  block, redact, flag, or monitor — as opposed to passing content through untouched.
- **Tier1**: The static, deterministic detection layer — regex/keyword rule matching executed by
  the gateway scanning engine. In the target model Tier1 runs ONLY user-enabled policy rules.
- **Tier2**: The semantic detection layer — a guard/large-language model (Bedrock or configured
  provider) that inspects content and returns an action. Tier2 is opt-in and model-decided; it uses
  no policies.
- **Built_In_Pattern_Library**: The hardcoded detection content shipped in the gateway today —
  `ATTACK_PATTERNS` (the attack-family regexes) and the built-in default PII/secret pattern set —
  that runs automatically. This feature removes it as a default and re-homes it as Policy_Packages.
- **Scanning_Engine**: The gateway machinery that executes a rule against text and applies an action
  (the regex/keyword matcher, the `InputScanner` execution path, and `redact_all` masking). The
  engine is RETAINED; only the built-in default CONTENT it ran is removed.
- **Policy**: A user- or system-authored detection rule set evaluated by the gateway policy engine
  (`policy_engine.evaluate()`), scoped to an organization, carrying one or more Rules.
- **Rule**: A single detection unit within a Policy — a regex or keyword matcher plus a
  user-selected Action.
- **Action**: The enforcement outcome a Rule (or Tier2 verdict) selects — exactly one of `block`,
  `redact`, `flag`, or `monitor`. (`monitor`/`flag` observe-only; `redact` masks; `block`
  terminates.)
- **Policy_Package**: A named, seeded collection of Rules (for example the re-homed
  `command_injection` family, the PII/secret family, or the existing CISO package) that an
  organization can enable or disable as a unit, per org. Modeled on the existing CISO package
  (`Policy` with `is_system`, `enabled`, per-org `code`).
- **Enabled_Policy_Set**: The set of Policies and Policy_Packages an organization has explicitly
  enabled. When empty, Tier1 detects nothing for that organization.
- **Tier2_Enabled**: A per-organization toggle (control-plane/frontend setting, synced to the
  gateway as the `tier2_enabled` config key) that determines whether Tier2 runs for that
  organization across ALL surfaces, including the OpenAI-SDK-compatible surface.
- **OpenAI_SDK_Surface**: The OpenAI-SDK-compatible request surface of the gateway (the
  OpenAI-shaped chat/completions endpoints clients call with the OpenAI SDK), which must honor the
  same Tier2_Enabled setting as the native surface.
- **Detection_Surface**: Any request path on which Detection can run — the chat proxy (`/v1/chat`
  and the OpenAI_SDK_Surface), RAG (`/v1/rag`), embeddings (`/v1/embeddings`), MCP tool calls
  (`mcp_proxy`), and the streaming output guard.
- **Passthrough**: The state in which content is forwarded unchanged — no block, redact, flag, or
  monitor verdict is produced.
- **Zero_Policy_State**: An organization whose Enabled_Policy_Set is empty AND whose Tier2_Enabled
  is off. In this state every Detection_Surface is Passthrough.
- **Legacy_Scan_Toggle**: The pre-existing per-org config flags that forced the Built_In_Pattern_Library
  to run — `input_scan_enabled`, `output_scan_enabled`, `scan_block_on_injection`, `scan_block_on_pii`,
  and equivalents — which this feature removes as detection drivers.
- **Enforcement_Authority**: The gateway module (`enforcement.py`) that resolves the final Action
  from the available layers using the severity lattice (allow < monitor/flag < redact < block).

## Requirements

### Requirement 1: No default Tier-1 detection — zero-policy is passthrough

**User Story:** As an organization operator, I want the firewall to detect nothing unless I enable a
policy, so that I have full control over what is inspected, blocked, redacted, or flagged.

#### Acceptance Criteria

1. THE Firewall SHALL NOT ship any Built_In_Pattern_Library that runs automatically; no attack-family regex or PII/secret pattern SHALL execute at Tier1 unless it belongs to a Rule in the organization's Enabled_Policy_Set.
2. WHEN an organization is in the Zero_Policy_State, THE Firewall SHALL treat every Detection_Surface as Passthrough, producing no block, redact, flag, or monitor verdict for any input or output.
3. WHEN an organization's Enabled_Policy_Set contains one or more Rules, THE Firewall SHALL run at Tier1 only the Rules in that Enabled_Policy_Set and SHALL NOT run any rule that is not in it.
4. THE Firewall SHALL enforce zero mandatory detection: there SHALL be no always-on rule, category, or safety floor that produces a verdict independent of the Enabled_Policy_Set and Tier2_Enabled.
5. IF the gateway scanning engine has no enabled Rule that matches a given content, THEN THE Firewall SHALL produce no Tier1 verdict for that content (default outcome is allow/passthrough).
6. THE change SHALL apply to ALL Detection_Surfaces (chat proxy including the OpenAI_SDK_Surface, RAG, embeddings, MCP tool calls, and the streaming output guard) as a single coherent model, such that no Detection_Surface runs a Built_In_Pattern_Library default while another does not.

### Requirement 2: Tier-1 is policy-only, static, and deterministic

**User Story:** As a security engineer, I want Tier-1 to run exactly and only my configured
deterministic rules, so that Tier-1 behavior is predictable and fully attributable to my policies.

#### Acceptance Criteria

1. THE Firewall SHALL define Tier1 as the execution of static, deterministic Rules (regex/keyword matchers) drawn solely from the organization's Enabled_Policy_Set.
2. WHEN Tier1 evaluates content, THE Firewall SHALL apply, for each matching Rule, the Action selected by the user for that Rule (one of block, redact, flag, or monitor).
3. THE Firewall SHALL retain the Scanning_Engine (the regex/keyword matcher, the input-scan execution path, and `redact_all` masking) so that a user-authored Rule can be executed, while removing every built-in default rule as content that runs without being enabled.
4. WHERE two or more enabled Rules match the same content, THE Firewall SHALL resolve the final Action via the existing Enforcement_Authority severity lattice (allow < monitor/flag < redact < block), preserving current merge semantics.
5. THE Firewall SHALL NOT use any Tier2 model to make a Tier1 decision, and SHALL NOT use any Policy to make a Tier2 decision; the two layers SHALL remain distinct (Tier1 = policy-only, Tier2 = model-only).
6. IF a Tier1 Rule is malformed or fails to compile, THEN THE Firewall SHALL exclude that Rule from execution and record an error identifying the offending Rule, without falling back to any built-in default.

### Requirement 3: Tier-2 is opt-in, model-only, and governs all surfaces including the OpenAI SDK

**User Story:** As an operator, I want a single explicit switch that turns the semantic model scan on
or off for my org across every surface — including the OpenAI-SDK path — so that Tier-2 never runs
unless I choose it.

#### Acceptance Criteria

1. THE Firewall SHALL expose a per-organization Tier2_Enabled setting in the control plane and the frontend, and SHALL synchronize it to the gateway as the `tier2_enabled` configuration key.
2. WHEN Tier2_Enabled is off for an organization (the default), THE Firewall SHALL NOT execute any Tier2 model scan on any Detection_Surface for that organization, and Tier2 SHALL contribute no verdict.
3. WHEN Tier2_Enabled is off for an organization, THE OpenAI_SDK_Surface SHALL also run no Tier2 model scan for that organization, such that the OpenAI-SDK path and the native path honor the identical Tier2_Enabled setting.
4. WHEN Tier2_Enabled is on for an organization, THE Firewall SHALL execute the Tier2 model scan and SHALL let the model alone decide the Action (allow, block, redact, or flag) for the scanned content.
5. THE Firewall SHALL NOT evaluate any Policy or Rule at Tier2; Tier2 SHALL be governed solely by the Tier2_Enabled toggle and the model's own verdict.
6. THE frontend SHALL present the Tier2_Enabled control with a clear enable/disable state and SHALL reflect the current backend value, such that toggling it in the frontend changes the gateway's Tier2 behavior for that organization.
7. IF the Tier2_Enabled setting cannot be resolved for a request (missing or unreadable configuration), THEN THE Firewall SHALL treat Tier2 as disabled for that request (fail toward no Tier2 detection, consistent with zero mandatory detection).

### Requirement 4: Built-in families re-homed as seeded, per-org toggleable policy packages

**User Story:** As an operator, I want the curated detection families available as packages I can
switch on or off for my org, so that I keep the value of the shipped rules without them running
unless I opt in.

#### Acceptance Criteria

1. THE Firewall SHALL provide the previously built-in detection families (for example prompt_injection, jailbreak, command_injection, sql_injection, data_leakage, path_traversal, and a PII/secret family) as one or more seeded Policy_Packages, using the existing system-policy seeding mechanism (a `Policy` with `is_system`, a per-org `code`, and an `enabled` flag, modeled on `seed_ciso_policy_package`).
2. THE Firewall SHALL seed every re-homed Policy_Package in the DISABLED state for every organization by default, such that seeding a package does not by itself cause any Detection.
3. WHEN an operator enables a Policy_Package for an organization, THE Firewall SHALL run that package's Rules at Tier1 with the Action selected for each Rule/family (block, redact, flag, or monitor).
4. WHEN an operator disables a Policy_Package for an organization, THE Firewall SHALL cease running that package's Rules at Tier1, returning those categories to Passthrough unless another enabled Policy covers them.
5. THE Firewall SHALL allow the user to select the Action per family/Rule within a Policy_Package (block, redact, flag, or monitor) and SHALL NOT impose a hardcoded Action on a package's Rules.
6. THE re-homing SHALL preserve the existing per-org Policy_Package mechanism's isolation, such that enabling or disabling a package for one organization SHALL NOT change detection for any other organization.

### Requirement 5: Remove the default-on scan toggles as detection drivers

**User Story:** As a maintainer, I want the legacy default-on scan flags gone, so that detection is
driven purely by policies and cannot be silently forced on by a stale configuration default.

#### Acceptance Criteria

1. THE Firewall SHALL remove the Legacy_Scan_Toggle flags (`input_scan_enabled`, `output_scan_enabled`, `scan_block_on_injection`, `scan_block_on_pii`, and equivalents) as drivers that force the Built_In_Pattern_Library to run.
2. THE Firewall SHALL NOT gate Tier1 detection on any configuration flag whose default value would cause detection in the Zero_Policy_State; Tier1 detection SHALL be gated solely on the Enabled_Policy_Set.
3. WHERE a removed Legacy_Scan_Toggle previously appeared in the control-plane configuration, the frontend, or the config-sync key set, THE Firewall SHALL remove or neutralize it so it no longer implies default detection, and SHALL not leave a flag whose presence re-enables built-in defaults.
4. IF a request arrives with a stale or legacy configuration that still contains a removed toggle, THEN THE Firewall SHALL ignore that toggle for the purpose of enabling detection and SHALL rely solely on the Enabled_Policy_Set and Tier2_Enabled.
5. THE Firewall MAY retain a single master bypass control only if it can never CAUSE detection (it may only suppress it); no retained flag SHALL be a detection driver.

### Requirement 6: All detection surfaces honor the model coherently

**User Story:** As a security reviewer, I want every request path to obey the same policy-driven
model, so that detection cannot leak in through a surface that still runs built-in defaults.

#### Acceptance Criteria

1. THE Firewall SHALL apply the policy-only-Tier1 and opt-in-Tier2 model on the chat proxy, the OpenAI_SDK_Surface, RAG, embeddings, MCP tool calls, and the streaming output guard.
2. WHEN an organization is in the Zero_Policy_State, THE Firewall SHALL Passthrough on the chat proxy, the OpenAI_SDK_Surface, RAG ingest and query, embeddings ingest, MCP tool arguments and results, and streamed output, producing no verdict on any of them.
3. WHERE a Detection_Surface previously ran a Built_In_Pattern_Library default (for example MCP result PII/secret redaction on by default, or embeddings input scan on by default), THE Firewall SHALL make that surface run detection only from the Enabled_Policy_Set (and Tier2 only when Tier2_Enabled).
4. THE Firewall SHALL keep the surfaces consistent, such that the same content with the same Enabled_Policy_Set and Tier2_Enabled produces the same Detection decision regardless of which Detection_Surface it arrives on (modulo surface-specific content shape).
5. IF a Detection_Surface cannot resolve the Enabled_Policy_Set for an organization, THEN THE Firewall SHALL treat that surface as Passthrough for that request (fail toward no detection), consistent with zero mandatory detection.

### Requirement 7: Clean cutover — no grandfathered defaults

**User Story:** As the plan owner, I want a single clean cutover to the policy-driven model, so that
all organizations move to the new model at once without a hidden legacy default path.

#### Acceptance Criteria

1. THE Firewall SHALL apply the policy-driven model to ALL organizations at cutover, with no per-org grandfathering that keeps the Built_In_Pattern_Library running as a default.
2. WHEN the cutover is applied, THE Firewall SHALL leave every organization that has not enabled any Policy_Package or Tier2 in the Zero_Policy_State (Passthrough).
3. THE Firewall SHALL document that after cutover an organization is unprotected until it enables at least one Policy_Package or Tier2, so operators understand the opt-in security posture.
4. THE Firewall SHALL NOT retain any code path that reintroduces built-in default detection for any organization after cutover.
5. WHERE an organization previously relied on default detection, THE cutover SHALL make no attempt to auto-enable packages on its behalf unless a separate, explicit migration decision is taken (out of scope for this feature).

### Requirement 8: Verification, evidence, and no regression to the retained machinery

**User Story:** As a maintainer, I want proof that the model behaves as specified and that the
retained policy/enforcement machinery still works, so that the cutover is safe and reviewable.

#### Acceptance Criteria

1. THE Firewall SHALL be verified by tests demonstrating that an organization in the Zero_Policy_State passes representative attack, PII, and secret inputs through every Detection_Surface with no block, redact, or flag.
2. THE Firewall SHALL be verified by tests demonstrating that enabling a re-homed Policy_Package causes its Rules to run at Tier1 with the user-selected Action, and disabling it returns those categories to Passthrough.
3. THE Firewall SHALL be verified by tests demonstrating that Tier2_Enabled off yields no Tier2 execution on the native surface AND the OpenAI_SDK_Surface, and Tier2_Enabled on yields model-decided Actions.
4. THE Firewall SHALL be verified by end-to-end checks across all Detection_Surfaces (chat, OpenAI SDK, RAG, embeddings, MCP, streaming output), not unit tests alone, confirming the Zero_Policy_State Passthrough and the enabled-package detection on each surface.
5. WHEN the gateway test gate (`cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests -q`) is run after the change, THE retained Scanning_Engine, policy engine, and Enforcement_Authority machinery SHALL still pass their tests, with pre-existing tests updated only where they asserted the now-removed built-in default behavior.
6. IF any test proves a Built_In_Pattern_Library default still fires in the Zero_Policy_State on any Detection_Surface, THEN the feature SHALL be treated as not meeting Requirement 1 until that default is removed.
7. THE feature SHALL record evidence (a committed artefact and/or the changelog protocol entries the repo requires) documenting the before/after behavior on each Detection_Surface and confirming the intentional cutover to zero mandatory detection.
8. THE feature SHALL NOT be considered complete on unit tests alone; the mandatory live end-to-end verification defined in Requirement 9 SHALL be performed and SHALL pass.

### Requirement 9: Mandatory live end-to-end verification with real services (Docker) — 100+ prompts, frontend + OpenAI SDK

**User Story:** As the plan owner, I want the policy-driven model proven against the actual running
system — real services started under Docker, driven by 100+ prompts through both the frontend and
the OpenAI SDK — so that "no policies means no scanning, and an enabled policy runs only its own
rules" is demonstrated in production-like conditions, not just in unit tests.

This is a **mandatory** acceptance requirement: the feature SHALL NOT be marked complete until every
criterion below is satisfied and its results are recorded.

#### Acceptance Criteria

1. THE verification SHALL start the ACTUAL services using Docker (the full stack — gateway, control
   plane, frontend, and their dependencies — via the repository's Docker Compose configuration),
   and SHALL confirm every started service reports healthy before any prompt is sent.
2. THE verification SHALL exercise the running system with at least 100 distinct input prompts that
   span the detection categories the re-homed Policy_Packages cover (for example prompt injection,
   jailbreak, command injection, SQL injection, data leakage, PII, and secrets) and include benign
   prompts, such that both true-positive and true-negative behavior is measured.
3. WHEN all Policy_Packages are OFF for the organization and Tier2_Enabled is off (the
   Zero_Policy_State), THE verification SHALL send the full 100+ prompt set through the running
   services and SHALL confirm that NONE of them is scanned, blocked, redacted, or flagged — every
   prompt and response passes through untouched (Passthrough) on the live stack.
4. WHEN exactly one Policy_Package is enabled for the organization, THE verification SHALL send the
   full prompt set through the running services and SHALL confirm that ONLY the enabled package's
   rules fire — prompts matching that package's enabled rules receive the user-selected Action
   (block/redact/flag/monitor), and prompts that would only have matched a DISABLED package or a
   non-enabled rule pass through untouched.
5. THE verification SHALL repeat criterion 4 for each re-homed Policy_Package independently (enable
   one at a time), confirming in each run that the other packages' rules do NOT fire, so that
   per-policy isolation is proven on the live stack.
6. THE verification SHALL drive the running system through BOTH the frontend surface AND the
   OpenAI-SDK-compatible surface with the same prompt set and the same policy configurations, and
   SHALL confirm identical policy-driven behavior on both surfaces (including that Tier2_Enabled off
   yields no Tier2 on either surface, and an enabled package's rules fire identically on both).
7. THE verification SHALL return and record the results — per prompt, the surface used, the policy
   configuration in effect, the decision observed (passthrough/block/redact/flag/monitor), and
   whether it matched the intended outcome — as a committed evidence artefact under a permitted path.
8. THE end-to-end run SHALL demonstrate that ALL prompts behave as intended under each configuration:
   zero detections when all policies are off, and only the enabled policy's rules active when a
   policy is enabled; any prompt whose observed decision does not match its intended outcome SHALL be
   treated as a failure that blocks completion until resolved.
9. IF any service fails to start or become healthy, or any of the 100+ prompts produces a decision
   that contradicts the configured policy state (a detection while all policies are off, or a rule
   from a disabled/other package firing), THEN the feature SHALL be treated as NOT meeting its
   acceptance criteria until the live end-to-end run passes cleanly on both the frontend and the
   OpenAI-SDK surfaces.
10. THE verification SHALL be reproducible: the evidence artefact SHALL record the exact commands to
    start the Docker services, seed/enable the Policy_Packages and Tier2 toggle per run, drive the
    prompts through the frontend and OpenAI SDK, and collect the results, sufficient for an
    independent reviewer to reproduce the live run.
