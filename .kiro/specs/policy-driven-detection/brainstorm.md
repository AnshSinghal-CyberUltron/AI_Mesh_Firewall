# Brainstorm: Policy-Driven Detection (no default rules)

**Status:** Exploration / alignment doc. NOT requirements or design yet. No code changed.
**Goal stated by user:** The firewall must NOT ship default detection rules that scan every
prompt. Detection happens **only** when a user-defined policy says so. With **no policies
configured, there is no detection** — nothing is blocked, redacted, or flagged. It is fully in
the user's control what to detect and whether to block / redact / allow.

This doc grounds that goal in the actual codebase, lays out the design forks with tradeoffs, and
surfaces the decisions to make before we commit to a requirements spec.

---

## 1. How detection works today (evidence)

### 1.1 The built-in Tier-1 scanner runs BY DEFAULT, independent of policies

- `gateway/ai_mesh_gateway/scanner.py` defines `ATTACK_PATTERNS: dict[str, list[str]]` — a
  hardcoded library of regexes for `prompt_injection`, `jailbreak`, `data_leakage`,
  `goal_hijacking`, `tool_overreach`, `sql_injection`, `command_injection`, `path_traversal`,
  `vector_injection`, plus `PII_PATTERNS` / secret detectors.
- On a match, `InputScanner._scan_prompt_sync` returns
  `ScanVerdict(action="block", confidence=1.0, tier="tier_1")` from the FIRST matching category
  and **short-circuits** (terminal, never reaches Tier-2).
- The gate that decides whether this runs (`main.py` ~L6987):
  ```
  _input_scan_will_run = (
      INPUT_SCANNER is not None
      and org_config.get("firewall_enabled") is not False
      and org_config.get("input_scan_enabled", True)   # default TRUE
  )
  ```
  It runs for **every org by default**, whether or not the org has authored any policy.

### 1.2 There ARE per-org toggles — but they default ON and are policy-independent

From `config.py` / `config_sync.py` (per-org, pushed from the control plane):

| Flag | Default | Effect |
| --- | --- | --- |
| `firewall_enabled` | `true` | Master switch. `is False` = chat passes through unscanned. |
| `input_scan_enabled` | `true` | Runs Tier-1 input scan (`ATTACK_PATTERNS` + PII). |
| `output_scan_enabled` | `true` | Runs output guard. |
| `scan_block_on_injection` | `true` | Injection match -> block. |
| `scan_block_on_pii` | `false` | PII -> block (else redact). |
| `mcp_redact_result_on_detect` | `true` | MCP tool result secret/PII -> redact. |

**Observation:** the toggles already exist, but "no policies" is NOT the thing that turns
detection off — the `input_scan_enabled=true` default is. So a fresh org is fully scanned by the
built-in library regardless of its (possibly empty) policy set.

### 1.3 The enforcement authority already merges policy + scanner (precedence exists)

`enforcement.py` `resolve_enforcement()` / `resolve_and_enforce()`:
- Precedence (highest wins): **org policy action > guard recommendation (Tier-1/Tier-2) > default
  (`allow`)**.
- So a policy CAN already override the scanner. What it can't do today is *suppress the scanner
  from producing a recommendation in the first place* — the scanner fires on its own.

### 1.4 There is already a full user policy engine

`policy_engine.py` `evaluate(prompt, response_text, compiled_policies, ...) -> EvaluationResult`
matches user-authored rules (regex / keyword / actor-scoped) and returns a winning action
(`block` / `redact` / `rewrite` / `flag` / `monitor` / `allow`) plus `redaction_fields`,
`matched_rules`, etc. Policies are compiled in the control plane and pushed to the gateway
(POLICY_SYNC). **This is the mechanism the "policy-driven" model would lean on.**

### 1.5 Detection runs on multiple surfaces (not just chat)

The built-in scanner / PII redaction is invoked on: chat proxy (`/v1/chat`), RAG (`/v1/rag`),
embeddings (`/v1/embeddings`), MCP tool calls (`mcp_proxy.py` scan orchestrator), and the
streaming output guard (`SecureStreamingResponse`). A "no default detection" change must be
coherent across all of them or the model leaks through a side door.

### 1.6 Prior design decision this reverses

Memory (`oss-guardrail-repos-ranked-2026-08-18`): "Keep T1 regex" was a deliberate stack choice.
Making detection fully opt-in reverses that default-on stance — worth an explicit, recorded
decision.

---

## 2. The core security tradeoff (must be acknowledged)

Making detection fully policy-driven means **a brand-new org, or any org with zero policies,
blocks/redacts NOTHING** — every prompt and response passes through untouched. That is a
deliberate "opt-in security / secure-only-when-configured" stance. It is a legitimate product
model (many WAFs, DLP, and policy engines are opt-in), but it is the opposite of "secure by
default." The single most important decision below (Decision A) is whether that is truly intended,
and whether there is any non-negotiable safety floor.

---

## 3. Design forks

### Fork 1 — What STOPS firing by default (the detection scope)

- **1A. Everything.** Tier-1 `ATTACK_PATTERNS` + PII/secret detection + Tier-2 semantic all become
  opt-in. Zero policies = pure passthrough. (Maximal user control; a fresh org leaks PII.)
- **1B. Attack patterns only.** Injection/command/sql/etc. become opt-in; PII/secret redaction
  stays on by default (a data-protection floor). (Protects PII by default; attack detection is
  opt-in.)
- **1C. Only the over-matching families.** Just neutralize the known false-positive shapes
  (command_injection backtick, data_leakage wildcards) — i.e. the original G0.3/G0.4 scope. (Small;
  does NOT achieve "no default rules.")

### Fork 2 — How a user opts INTO a built-in detector (the mechanism)

- **2A. Delete the built-in pattern library.** Remove `ATTACK_PATTERNS` shipped patterns entirely;
  users author their own regex/keyword rules via the existing policy engine. Detection = 100% user
  content. (Purest "user control"; users lose the curated injection/jailbreak library and must
  rebuild it; highest migration cost.)
- **2B. Built-in families ship but default OFF; a policy/config enables a family.** Keep the
  curated library as a catalog of toggleable detectors (e.g. "enable `command_injection`
  detection", "enable PII redaction with action=redact"). A family only runs when a policy/config
  references it. (Keeps the curated value; users opt in per family; new "detector catalog" concept.)
- **2C. Hybrid.** Built-in families are selectable presets a user turns on, PLUS custom rules. (Most
  flexible; largest surface.)

### Fork 3 — Mechanism placement (how we make the scanner stop by default)

- **3A. Flip the existing toggle defaults to OFF.** `input_scan_enabled` / `output_scan_enabled`
  (and/or `firewall_enabled`) default `false`; "having policies" (or an explicit enable) turns them
  on. Smallest code change; reuses the existing gate at `main.py:6987`. Risk: coarse — it's all-or-
  nothing per surface, not per-family; and it conflates "firewall off" with "no policies."
- **3B. Gate the built-in scanner on policy presence/opt-in inside the pipeline.** The built-in
  scanner only contributes a recommendation for a category when a policy enables that category;
  otherwise its verdict is suppressed before it reaches `resolve_and_enforce`. Cleaner semantics
  (per-family, policy-driven), larger change (touches the scan->enforce seam on every surface).
- **3C. Remove the built-in scanner from the enforcement lattice entirely.** Only
  `org_policy_action` + default-allow remain; the built-in guard no longer feeds a recommendation.
  Paired with 2A (delete library) or 2B (families become policies). Most aligned with "fully policy-
  driven"; biggest blast radius.

### Fork 4 — Backward compatibility / rollout

- **4A. Clean global cutover.** All orgs get the new default (no detection without policies).
  Simplest; changes behaviour for the live `zeroshield` deployment and any existing customer relying
  on default detection.
- **4B. Grandfather existing orgs.** Existing orgs keep current behaviour (or are migrated to
  explicit "enable all built-in families" policies); only NEW orgs default to no-detection. Safer;
  needs a migration + a per-org "detection profile" default.
- **4C. Feature-flagged.** New model behind a flag; opt-in per org during rollout, then flip the
  default. Safest; most process.

### Fork 5 — Safety floor

- **5A. No floor.** 100% user control; even a detected private key egresses if no policy says
  otherwise.
- **5B. Minimal non-negotiable floor.** A tiny always-on set (e.g. never egress a detected private
  key / block a known catastrophic payload) regardless of policies. Contradicts "fully in user
  control" but bounds worst-case liability.

---

## 4. Interaction with the in-flight G0.3 spec

`.kiro/specs/command-injection-fp-fix/` (G0.3) narrows ONE over-matching pattern
(`command_injection` backtick). If we adopt a policy-driven model where `command_injection` is OFF
by default (Fork 1A/1B + 2B/3B), the false positive disappears wholesale (no policy = no
command_injection block), so G0.3 becomes redundant or folds in as "the command_injection built-in
family, when a user enables it, uses a narrowed backtick pattern." Options:
- **Park/supersede G0.3** and fold its intent into this effort.
- **Keep G0.3 as a narrow interim fix** (ships fast) and treat policy-driven detection as the
  larger follow-on.

---

## 5. Recommended starting position (for discussion — not locked)

A defensible, incremental target that achieves the user's goal without a reckless big-bang:

- **Fork 1: 1A** — all built-in detection becomes opt-in (true "no default rules"), BUT see Fork 5.
- **Fork 2: 2B** — keep the curated library as a **catalog of built-in detector families that
  default OFF**; a user turns a family on (with a chosen action: block/redact/flag/monitor) via the
  existing policy/config plane. Preserves the curated injection/jailbreak value while making it
  100% opt-in. (2A "delete the library" only if you want zero shipped detection content at all.)
- **Fork 3: 3B** — gate the built-in scanner's contribution on the per-family enable; the
  enforcement lattice already merges policy > guard > allow, so a suppressed family simply
  contributes nothing = default allow.
- **Fork 4: 4B or 4C** — grandfather/flag existing orgs so the live deployment isn't silently
  opened; new orgs default to no-detection.
- **Fork 5: your call** — 5A (pure user control, matches your words literally) vs 5B (a tiny
  always-on floor for catastrophic egress). This is the one I most want your explicit answer on,
  because it defines worst-case behaviour.

Rationale: this reuses the existing policy engine + enforcement lattice + per-org config sync
(low architectural risk), delivers "no policies = no detection," keeps the curated detectors as
opt-in value rather than throwing them away, and stages the rollout so no live customer is
surprised. If you truly want ZERO shipped detection content (users build everything), switch Fork 2
to 2A and Fork 3 to 3C.

---

## 6. Open questions to resolve before requirements

1. **Fork 5 (safety floor):** truly zero mandatory detection, or a minimal always-on catastrophic
   floor?
2. **Fork 2 (library fate):** keep the curated detector families as opt-in (2B), or delete the
   built-in pattern library entirely and require users to author all rules (2A)?
3. **Fork 1 (PII):** does "no default detection" include PII/secret redaction being OFF by default
   (1A), or should PII protection remain a default (1B)?
4. **Fork 4 (existing orgs):** clean cutover, grandfather, or feature-flag — and what happens to the
   live `zeroshield` org?
5. **Surfaces:** apply the model to ALL entry points (chat, RAG, embeddings, MCP, streaming output)
   at once, or chat first?
6. **G0.3:** park/supersede it, or keep it as an interim narrow fix?
7. **"Enable a family" UX/data model:** is turning on a built-in family a new kind of policy, a
   config flag per family, or a preset? (Affects the control-plane + gateway data model.)
8. **Enforcement semantics when a family IS enabled:** does the user pick the action per family
   (block/redact/flag/monitor), and does per-category org policy still override it via the existing
   lattice?

Once these are answered we can commit to `requirements.md` for the `policy-driven-detection` spec.
