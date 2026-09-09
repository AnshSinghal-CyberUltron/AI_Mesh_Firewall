# Design Document

## Overview

This design turns AI Mesh Firewall detection into a **fully policy-driven** system with **no default
rules**. Today the gateway runs a hardcoded Tier-1 library (`scanner.ATTACK_PATTERNS` + built-in
PII/secret patterns) on every request whenever `input_scan_enabled` is true (its default), producing
an independent `ScanVerdict` that the enforcement authority merges with policy actions. The target
model:

- **Tier-1 = policy only.** The built-in pattern library is removed as an automatic scanner. Tier-1
  detection is exclusively the rules of the organization's **enabled policy packages**, executed by
  the existing `policy_engine.evaluate()`. Zero enabled policies ⇒ zero Tier-1 detection.
- **Tier-2 = model only, opt-in.** The semantic (Bedrock/guard-model) scan runs only when the
  per-org `tier2_enabled` toggle is on (default OFF). When on, the model alone decides the action.
  The same toggle governs the OpenAI-SDK surface because it shares the same pipeline.
- **Built-in families re-homed as seeded, per-org, default-OFF policy packages** (modeled on the
  existing CISO package seeder).
- **Legacy default-on scan toggles removed** as detection drivers.
- **Clean cutover, all surfaces** (chat, OpenAI-SDK, RAG, embeddings, MCP, streaming output).
- **Zero mandatory detection** — no safety floor.

The strategy reuses three components that already exist and are correct: the **policy engine**
(`policy_engine.evaluate`), the **enforcement authority** (`enforcement.resolve_and_enforce` /
`enforce_output`), and the **config-sync** per-org key mechanism (`config_sync.py`, which already
carries a nullable tri-state `tier2_enabled`). The change removes the built-in scanner's *content*
and its *independent contribution*, not the machinery.

### Requirements traceability (summary)

| Requirement | Where addressed |
| --- | --- |
| R1 No default Tier-1; zero-policy passthrough | §"Tier-1 becomes policy-only", §"Pipeline seam changes" |
| R2 Tier-1 policy-only/static/deterministic | §"Tier-1 becomes policy-only", §"Enforcement authority" |
| R3 Tier-2 opt-in, model-only, all surfaces incl. OpenAI SDK | §"Tier-2 opt-in gating", §"OpenAI-SDK surface" |
| R4 Built-in families re-homed as seeded default-OFF packages | §"Re-homing the built-in library as policy packages" |
| R5 Remove legacy scan toggles | §"Removing the legacy scan toggles" |
| R6 All surfaces coherent | §"Per-surface changes" |
| R7 Clean cutover | §"Cutover & migration" |
| R8 Verification / no regression | §"Testing strategy" |
| R9 Mandatory live E2E (Docker, 100+ prompts, frontend + OpenAI SDK) | §"Live end-to-end verification harness" |

## Architecture

### Current flow (input side, chat / OpenAI-SDK — `proxy_chat`)

```
request (/v1/chat/completions — native AND OpenAI SDK share proxy_chat)
  -> auth, model allowlist
  -> _input_scan_will_run = INPUT_SCANNER and firewall_enabled!=False and input_scan_enabled(default True)
  -> _policy_check_cached(prompt) -> policy_engine.evaluate() -> org_policy_action, matched_rules
  -> INPUT_SCANNER.scan_prompt(text)  -> ScanVerdict (ATTACK_PATTERNS + PII)   [BUILT-IN DEFAULT]
  -> [Tier-2] scan_prompt_with_tier2() when tier2 applies
  -> resolve_and_enforce(scanner_*, org_policy_action, ...) -> PipelineDecision
  -> block / redact / allow
```

The problem: `INPUT_SCANNER.scan_prompt` emits a `ScanVerdict` from the hardcoded `ATTACK_PATTERNS`
**independent of any policy**, and `resolve_and_enforce` treats that verdict as a first-class guard
recommendation. So a zero-policy org still gets blocked.

### Target flow

```
request
  -> auth, model allowlist
  -> resolve org detection config: enabled policy packages + tier2_enabled (default OFF)
  -> Tier-1: policy_engine.evaluate(prompt, "", enabled_compiled_policies) -> per-rule action(s)
       (NO built-in ATTACK_PATTERNS scan; scanner engine only runs rules that came from policies)
  -> Tier-2: ONLY if tier2_enabled -> model scan -> model action (allow/block/redact/flag)
  -> resolve_and_enforce(policy_action, tier2_action, default=allow) -> PipelineDecision
  -> zero enabled policies AND tier2 off  => no recommendation => allow (passthrough)
```

Key architectural decision: **the built-in `ScanVerdict` is removed as an input to the enforcement
authority.** Detection recommendations now come from exactly two sources — (1) the policy engine
(Tier-1) and (2) the Tier-2 model (when enabled). The enforcement lattice already supports
"policy > recommendation > default-allow," so a suppressed/absent recommendation naturally yields
allow.

## Components and Interfaces

### 1. Tier-1 becomes policy-only (remove the built-in default scan)

**Change locus:** `scanner.py`, `main.py` (the `_input_scan_will_run` seam and the scan call),
and the equivalent scan invocations on RAG/embeddings/MCP/streaming.

**Design:**

- Remove `ATTACK_PATTERNS` and the built-in default PII/secret pattern set as **content that runs
  automatically**. The `InputScanner` class, `compile_pattern`, the matching loop, `redact_all`,
  and the deobfuscation/unicode machinery are **retained** — they become the *executor* the policy
  engine drives, not an autonomous scanner.
- The chat pipeline no longer calls `INPUT_SCANNER.scan_prompt(text)` to obtain an independent
  built-in verdict. Instead:
  - Tier-1 detection = `policy_engine.evaluate(prompt, "", compiled_policies)` over the org's
    **enabled** compiled policies (this call already exists via `_policy_check_cached`).
  - When a policy Rule is a regex/keyword matcher (the re-homed families and user rules), the
    engine matches it; the winning per-rule action becomes the Tier-1 recommendation.
- `resolve_and_enforce(...)` is called with `scanner_*` fields **empty/None** (no built-in guard
  recommendation) and `org_policy_action` = the policy engine's action. With no matched policy and
  Tier-2 off, all inputs to the lattice are allow/None ⇒ **allow (passthrough)**.

**Why not just flip `input_scan_enabled` to False (Fork 3A)?** Because the requirement is per-family
policy control and "no built-in content," not a coarse master off-switch. Removing the built-in
verdict at the enforcement seam (Fork 3B/3C) is what makes "enable one package ⇒ only that package's
rules fire" true.

**Interface change (scanner):**

- `InputScanner.scan_prompt` / `_scan_prompt_sync`: no longer iterate `ATTACK_PATTERNS`. Either
  (a) the built-in category loop is deleted and the method becomes a thin executor used only for
  policy-driven redaction, or (b) the method is retained but only ever invoked with an explicit
  ruleset argument (never the module-level built-in dict). The design prefers **(a) delete the
  built-in default categories** and drive all matching through `policy_engine`.

### 2. Re-homing the built-in library as policy packages

**Change locus:** `control/ai_mesh_control/policy/` — new seeder(s) modeled on
`ciso_seed.py` / `ciso_policy_catalog.py`.

**Design:**

- Convert each built-in attack family (prompt_injection, jailbreak, command_injection, sql_injection,
  data_leakage, path_traversal, goal_hijacking, tool_overreach, vector_injection) and a PII/secret
  family into **seeded system Policy_Packages** — a `Policy` row per family (or one package with
  per-family rules), with `is_system=True`, a per-org `code`, and `enabled=False` by default.
- Each shipped regex becomes a `Rule` with `rule_type="regex"` (or keyword), a default `action` the
  operator can change (block/redact/flag/monitor), and the family name as its category.
- Seeding is **idempotent** and **default-OFF** (unlike `seed_ciso_policy_package` which seeds
  `enabled=True` — the new seeder seeds `enabled=False`).
- The compiler/`policy_sync` path that already pushes compiled policies to the gateway Redis cache
  is reused unchanged — an enabled package simply appears in `POLICY_SYNC.get_policies(org_slug)`.
- The command_injection package's backtick rule uses the **narrowed** pattern from the parked G0.3
  spec as its default rule (folding G0.3 in).

**Data model:** reuse the existing `Policy` / `Rule` models (no schema change expected). A package
is identified by `category` + `is_system=True` + per-org `code`; `enabled` is the toggle.

### 3. Tier-2 opt-in gating (model-only, all surfaces)

**Change locus:** `config.py` / `config_sync.py` defaults, `main.py` Tier-2 invocation, control
plane + frontend toggle, OpenAI-SDK surface (shared pipeline).

**Design:**

- `tier2_enabled` already exists as a **nullable tri-state** config key (`config_sync._NULLABLE_KEYS`).
  The design sets the **effective default to OFF**: when the resolved value is `None`/absent, Tier-2
  does not run. (Today the code path treats `None` as "no per-org opinion" and can fall through to a
  global default — that fall-through must resolve to OFF.)
- The Tier-2 invocation in `proxy_chat` (`scan_prompt_with_tier2` / the sync_pre_llm path at
  main.py ~L8325) is gated so it executes **only** when the resolved `tier2_enabled` is true.
- Tier-2 remains **model-only**: it takes no policy. Its returned action (allow/block/redact/flag)
  is passed to `resolve_and_enforce` as the recommendation. No policy evaluation feeds Tier-2.
- Because `/v1/chat/completions` (the OpenAI-SDK surface) and the native surface share `proxy_chat`,
  gating Tier-2 in the shared pipeline automatically covers the OpenAI-SDK path (R3.3). A test
  asserts both surfaces observe identical Tier-2 on/off behavior.
- The RAG Tier-2 (`rag_tier2_enabled`) and MCP Tier-2 (`mcp_tier2_enabled`) toggles follow the same
  default-OFF rule for their surfaces.

**Control plane + frontend:** expose `tier2_enabled` as an explicit enable/disable control (it is
already a synced config key), ensuring the frontend reflects the backend value and toggling it
changes gateway behavior (R3.1, R3.6). This spans control API + a frontend settings control.

### 4. Enforcement authority (unchanged core, changed inputs)

`enforcement.resolve_and_enforce` / `enforce_output` are **not restructured** — they already merge
`org_policy_action` > guard recommendation > default-allow via the severity lattice. The change is
purely in what is passed:

- `scanner_action` / `scanner_recommendation` / `scanner_threat_type`: **None** (no built-in guard).
- `org_policy_action`, `matched_rules`, `matched_policy_names`: from `policy_engine.evaluate` over
  enabled policies (Tier-1).
- Tier-2 recommendation: passed only when `tier2_enabled`.
- With all inputs None/allow ⇒ `PipelineDecision(action="allow")` ⇒ passthrough.

`enforcement_mode` (block vs monitor) remains an org posture that can downgrade a resolved block to
monitor — it is orthogonal to "which detections run" and is retained.

### 5. Removing the legacy scan toggles

**Change locus:** `config.py`, `config_sync.py` (`_BOOL_KEYS`), `main.py` gates, frontend.

**Design:**

- Remove `input_scan_enabled`, `output_scan_enabled`, `scan_block_on_injection`, `scan_block_on_pii`
  (and RAG/embeddings equivalents) as **detection drivers**. The gate `_input_scan_will_run` and its
  siblings are replaced by "does the org have any enabled Tier-1 policy for this surface?" (which is
  simply: does `policy_engine.evaluate` over enabled policies produce a match).
- `firewall_enabled` MAY be retained **only** as a master **bypass** (it can suppress the whole
  pipeline, never cause detection). Any retained flag must be suppression-only (R5.5).
- Removed keys are dropped from `config_sync._BOOL_KEYS`, from control-plane config serialization,
  and from the frontend settings UI; a stale value arriving from an old control plane is ignored for
  enabling detection (R5.4).

### 6. Per-surface changes (coherence)

Each Detection_Surface currently has its own built-in-scan invocation; each must become policy-driven:

| Surface | Current built-in default | Change |
| --- | --- | --- |
| Chat proxy (`proxy_chat`) + OpenAI SDK | `INPUT_SCANNER.scan_prompt` + output guard by default | Drive Tier-1 from enabled policies only; Tier-2 gated by `tier2_enabled` |
| Streaming output guard (`SecureStreamingResponse`) | Output scan on when `output_scan_enabled` | Runs only enabled output policies; no default patterns |
| RAG (`/v1/rag`, `rag_pipeline`) | Input scan + Tier-2 on ingest/query | Tier-1 from enabled policies; `rag_tier2_enabled` default OFF |
| Embeddings (`/v1/embeddings`) | `_scan_redact_embedding_inputs` gated by `input_scan_enabled` (default on) | Redact only when an enabled policy targets it |
| MCP tool calls (`mcp_proxy`, scan orchestrator) | `mcp_redact_result_on_detect` default ON; built-in arg/result scan | Detection from enabled policies only; `mcp_tier2_enabled` default OFF |

All surfaces converge on the same two recommendation sources (enabled policies + opt-in Tier-2), so
the same content + config yields the same decision on any surface (R6.4).

### 7. OpenAI-SDK surface

No separate code path is needed: `/v1/chat/completions` (what the OpenAI SDK calls) is served by the
same `proxy_chat` handler and the same middleware (`_openai_compat_shim`). Because Tier-1 gating and
Tier-2 gating happen inside that shared handler, the OpenAI-SDK surface inherits the policy-driven
model automatically. The design adds explicit tests (native + OpenAI SDK) to prove parity rather than
new gating code.

## Data Models

No new gateway data model is required (the enforcement `PipelineDecision`, `EvaluationResult`, and
config dict are reused). Control-plane side:

- **Re-homed packages:** reuse `Policy` (`is_system`, `category`, per-org `code`, `enabled`,
  `priority`, `metadata`) and `Rule` (`rule_type`, `condition`, `action`, `name`, `description`).
  A new catalog module (analogous to `ciso_policy_catalog.py`) enumerates each family's rules; a new
  idempotent seeder (analogous to `ciso_seed.py`) creates them **`enabled=False`**.
- **Config keys:** `tier2_enabled` (existing, nullable tri-state → effective default OFF);
  `rag_tier2_enabled`, `mcp_tier2_enabled` (existing → default OFF). Removed keys:
  `input_scan_enabled`, `output_scan_enabled`, `scan_block_on_injection`, `scan_block_on_pii`.

## Error Handling

- **Fail toward no detection (consistent with zero mandatory detection).** If the enabled-policy set
  cannot be resolved for a request, the surface is treated as Passthrough for that request (R6.5). If
  `tier2_enabled` cannot be resolved, Tier-2 is treated as disabled (R3.7). This inverts the current
  fail-closed default for the built-in scanner, matching the opt-in security posture.
- **Malformed policy rule** → excluded from execution, error recorded, no fallback to a built-in
  default (R2.6).
- **Policy cache unavailable** (`POLICY_SYNC` not loaded) → no enabled Tier-1 rules resolvable →
  Passthrough (there is no built-in library to fall back to). Note this is a deliberate behavior
  change from today's `policy_cache_require_loaded` fail-closed; the design documents it.

## Testing Strategy

Two layers, both required (R8 + R9).

### Unit / integration (gateway test gate)

- **Zero-policy passthrough:** with an empty enabled-policy set and Tier-2 off, representative attack,
  PII, and secret inputs pass through every surface with `action="allow"` and no redaction (R8.1).
- **Enable a package ⇒ only its rules fire:** enabling a re-homed package causes its rules to match
  with the user-selected action; disabling returns to passthrough (R8.2).
- **Per-package isolation:** enabling package A does not fire package B's rules.
- **Tier-2 gating:** `tier2_enabled` off ⇒ `scan_prompt_with_tier2` not invoked on native AND OpenAI-
  SDK requests; on ⇒ model action honored (R8.3).
- **Enforcement authority:** with `scanner_*`=None and `org_policy_action`=None ⇒
  `PipelineDecision(action="allow")` (lattice regression lock).
- **Legacy toggles ignored:** a stale `input_scan_enabled=true` in config does not cause detection.
- **Gateway gate green:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests -q`;
  pre-existing tests updated only where they asserted the removed built-in default behavior (R8.5).

### Live end-to-end verification harness (R9 — MANDATORY)

A dedicated harness (script + evidence artefact) that:

1. **Starts the real stack via Docker Compose** (gateway, control plane, frontend, Redis/Postgres,
   any deps) and waits for every service healthcheck to report healthy before sending traffic (R9.1).
2. **A ≥100-prompt corpus** spanning every re-homed family (prompt injection, jailbreak, command
   injection, SQL injection, data leakage, PII, secrets) plus benign prompts, each with an
   `intended_outcome` per policy configuration (R9.2). (May reuse / extend the G0.1 detection corpus.)
3. **Run matrix:**
   - **Config A — all packages OFF, Tier-2 OFF:** send all 100+ prompts; assert **every** one is
     passthrough (no block/redact/flag) on the live stack (R9.3).
   - **Config B(i) — exactly one package ON** (repeat per package): assert only that package's rules
     fire with the selected action; prompts that would match only a disabled/other package pass
     through (R9.4, R9.5).
4. **Both surfaces:** drive the identical matrix through **the frontend** (its chat/test surface) and
   through **the OpenAI SDK** (`openai` client against `/v1/chat/completions`), asserting identical
   policy-driven behavior, including Tier-2 off on both (R9.6).
5. **Record results** per prompt (surface, config, observed decision, intended, pass/fail) into a
   committed evidence artefact under a permitted path (e.g. `docs/perf/` or the plan evidence dir),
   plus the exact reproduce commands (R9.7, R9.10).
6. **Completion gate:** all prompts must match intent under each config; any service-health failure
   or contradicting decision blocks completion (R9.8, R9.9).

Harness runs the frontend leg via Playwright (the repo's established E2E tool) and the OpenAI-SDK leg
via the `openai` Python client pointed at the gateway base URL with an org API key.

## Correctness Properties

These are the falsifiable invariants the implementation must uphold. Each is stated so it can be
asserted as a property-based or example test (unit layer) and re-confirmed on the live stack (R9).

### Property 1: Zero-policy passthrough

*For any* input content, if the organization's enabled policy set is empty AND `tier2_enabled` is
off, then the resolved `PipelineDecision.action` is `allow`, no redaction is applied, and no flag is
raised, on every Detection_Surface.

**Validates: Requirements 1.2, 6.2**

### Property 2: No built-in contribution

*For any* input, the enforcement authority receives no built-in scanner recommendation
(`scanner_action` / `scanner_recommendation` is None); every non-allow decision traces to a matched
enabled policy Rule or an enabled Tier-2 model verdict.

**Validates: Requirements 1.1, 1.4, 2.1**

### Property 3: Only enabled rules fire

*For any* input and *any* set of packages, a Rule produces a verdict if and only if it belongs to a
package that is enabled for that organization; a Rule in a disabled or non-enabled package never
contributes an action.

**Validates: Requirements 1.3, 4.3, 4.4**

### Property 4: Per-package isolation

*For any* input that would only match package B, enabling package A yields passthrough; the decision
depends only on the packages enabled for that organization, not on which packages exist or are
enabled for other organizations.

**Validates: Requirements 4.6, 2.4**

### Property 5: User-selected action is honored

*For any* matched enabled Rule, the resolved action (before org `enforcement_mode` downgrade) equals
the highest-severity user-selected action among matching rules per the lattice
`allow < monitor/flag < redact < block`; no hardcoded action is imposed.

**Validates: Requirements 2.2, 4.5**

### Property 6: Tier-2 opt-in gating

*For any* request, the Tier-2 model scan executes if and only if the resolved `tier2_enabled` is
true; when it does not execute it contributes no verdict; an unresolved or absent value resolves to
not-executing.

**Validates: Requirements 3.2, 3.7**

### Property 7: Surface parity for Tier-2

*For any* identical content and identical `tier2_enabled`, the native surface and the
OpenAI_SDK_Surface execute (or skip) Tier-2 identically and reach the same decision.

**Validates: Requirements 3.3, 6.4**

### Property 8: Tier-2 is model-only, Tier-1 is policy-only

*For any* request, no policy or Rule influences a Tier-2 decision, no Tier-2 verdict is produced when
`tier2_enabled` is off, and no model influences a Tier-1 decision.

**Validates: Requirements 2.5, 3.4, 3.5**

### Property 9: Legacy toggle inertness

*For any* configuration value of the removed `input_scan_enabled` / `output_scan_enabled` /
`scan_block_on_injection` / `scan_block_on_pii` keys, the detection decision is unchanged; those keys
never cause detection.

**Validates: Requirements 5.2, 5.4**

### Property 10: Fail toward no detection

*For any* request where the enabled-policy set cannot be resolved, the surface is passthrough; where
`tier2_enabled` cannot be resolved, Tier-2 does not run. No unresolved-state path produces a block,
redact, or flag.

**Validates: Requirements 6.5, 3.7, 1.4**

### Property 11: Surface coherence

*For any* identical content, enabled policy set, and `tier2_enabled`, the detection decision is the
same across chat, OpenAI SDK, RAG, embeddings, MCP, and streaming output, modulo surface-specific
content shape.

**Validates: Requirements 6.4**

### Property 12: Determinism and reproducibility

*For any* fixed input, fixed enabled policy set, and fixed `tier2_enabled=off`, Tier-1 evaluation is
deterministic and repeatable (identical decision across runs), and the live end-to-end run reproduces
the same per-prompt outcomes from the recorded commands.

**Validates: Requirements 9.8, 9.10**

## Rollout / Cutover

- **Clean cutover (R7):** ship the removal of built-in defaults + the seeded default-OFF packages +
  the Tier-2-default-OFF change together. Post-cutover, every org with no enabled package and Tier-2
  off is passthrough — including the live `zeroshield` deployment (called out as intended).
- **No grandfathering** and no auto-enable of packages on any org's behalf (R7.5). If the live org
  should keep protection, an operator explicitly enables the relevant packages (a separate, explicit
  action — out of scope here but documented as the required follow-up).
- **Changelog protocol:** the repo mandates four-memory changelog entries for pipeline/MCP changes;
  each pipeline-touching change records a `PIPELINE-xxxx` entry (and MCP surface changes a `CHG-xxxx`).

## Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| A live org silently loses all protection at cutover | Explicit documentation (R7.3) + operator runbook to enable packages; consider a pre-cutover comms/checklist (process, not code). |
| A surface still runs a built-in default (leak-through) | Per-surface audit table (§6) + R8/R9 tests that assert passthrough on EVERY surface in the zero-policy state; R8.6 makes a residual default a hard fail. |
| Fail-open on policy-cache-unavailable weakens security | Documented as the intended posture (opt-in security); if unacceptable, the org can run Tier-2 or the operator keeps the cache loaded. Flagged as a deliberate behavior change from `policy_cache_require_loaded`. |
| Tier-2 `None` tri-state accidentally resolving ON | Explicit effective-default-OFF resolution + test asserting `None`/absent ⇒ Tier-2 does not run. |
| Removing config keys breaks a stale control plane | Removed keys ignored (not error) on the gateway; config-sync tolerates unknown/removed keys. |
| Re-homed regexes reintroduce the old false positives when enabled | Ship the narrowed command_injection backtick rule (folded G0.3) and validate packages against the G0.1 developer-traffic benign suite. |
