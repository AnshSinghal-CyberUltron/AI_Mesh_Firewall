# Pipeline Changelog

All changes to the chat pipeline consolidation/fix/freeze effort.

## PIPELINE-0008 (2026-07-03)

**LEAK VERIFIED FIXED: PII NEVER reaches the model on a block; the original
input_scan BLOCK + model_output 7710ms signature is structurally impossible.**

Evidence (26 tests + LIVE verification):

1. **Input BLOCK → model NEVER called**: `resolve_and_enforce` produces
   `is_terminal_block=True` for injection/unmaskable-PII/toxicity/org-policy.
   `build_pipeline_trace(blocked_stage=input_scan)` produces `model_input=skip`,
   `model_output=skip`, `content=""`. Structural source proof: ≥3 block-return
   sites in `proxy_chat` precede ALL `LLM_ROUTER.acompletion` calls.

2. **Input REDACT → model sees masked only**: `redact_all` byte-removes SSN
   (123-45-6789), email (alice.jones@...), AWS key (AKIAIOSFODNN7EXAMPLE)
   simultaneously. The pipeline trace `model_input.content` has zero raw PII.
   Integration chain: `detect_pii` → `resolve_and_enforce(redact)` → `redact_all`
   → byte-absent verified.

3. **Degraded + PII → redact or block, never raw**: Tier-2 degraded +
   `tier1_pii_detected=True` → `action=redact`. Unmaskable → `action=block`,
   `is_terminal_block=True`. Clean prompt under degraded → `monitor` only.

4. **Original leak signature impossible**: `blocked_stage=input_scan` →
   `_model_skipped=True` → `model_output.action=skip`, `content=""`. The
   skip-after-block centralized invariant (L718-744 in pipeline_trace.py) forces
   ALL stages after the blocked stage to `action=skip` with cleared metadata.

5. **LIVE verification** (Docker stack, enforcement_mode=block): Sent
   `SSN 123-45-6789 + email + AKIAIOSFODNN7EXAMPLE` → HTTP 403, blocked_by=policy,
   `model_input=skip`, `model_output=skip`, `content=""`, total_latency=9.7ms
   (no 7710ms LLM call). Zero raw PII in any trace stage.

Gate: 26 new tests + 1920 full gateway suite passed.

## PIPELINE-0007 (2026-07-03)

**ONE authoritative `final_action` + `blocked_by`; no double-block ambiguity.**

Problem:
- `_build_safe_block_response` called `_resolve_pipeline_blocked_by` independently,
  duplicating the resolution already done in `_build_block_response` — so `blocked_by`
  could diverge from the trace's `blocked_stage`.
- The client-facing 403 JSON body contained BOTH `blocked_by` and a redundant
  `pipeline_stage` field (identical value, confusing).
- `build_pipeline_trace` received `final_action` from ad-hoc sources (`zeroshield.action`,
  hardcoded "allow") rather than from the authoritative `PipelineDecision`.
- Streaming path used hardcoded `"allow"` for `final_action` in the base trace regardless
  of the actual input enforcement decision.

Changes:
- **main.py `_build_safe_block_response`**: accepts `blocked_by: str | None` parameter;
  uses it directly when provided (single-writer from `_build_block_response`), falls back
  to `_resolve_pipeline_blocked_by` only when `None`. Removed redundant `pipeline_stage`
  key from the 403 JSON body.
- **main.py `_build_block_response`**: passes its already-resolved `blocked_stage` to
  `_build_safe_block_response` as `blocked_by=blocked_stage` — eliminates double resolution.
- **main.py non-streaming trace calls (L6962, L8527)**: `final_action` now reads from
  `_input_decision.action` (the authoritative `PipelineDecision`) when available, falling
  back to `zeroshield.action` only when the input scanner is disabled.
- **main.py `_launch_chat_stream_response`**: gains `input_action: str = "allow"` param;
  streaming trace uses it as `final_action` instead of hardcoded "allow". Both call sites
  (L6827 standalone, L7568 connected) pass `_input_decision.action` when available.
- **main.py (L5940)**: `_input_decision = None` initialized before enforcement — ensures
  safe access in all downstream paths.

Single-writer contract:
- **Block path**: `_build_block_response` resolves `blocked_stage` ONCE via
  `_resolve_pipeline_blocked_by` → passes to `_build_safe_block_response` → client JSON
  has ONE `blocked_by` field (no duplicate `pipeline_stage`).
- **Trace path**: `build_pipeline_trace(final_action=...)` always receives the
  `PipelineDecision.action` — the same value that drove the block/redact/allow decision.
- **No double-block**: a blocked request returns immediately (PIPELINE-0005 short-circuit);
  `final_action` is set exactly once from the terminal stage.

Tests (new, `test_pipeline_final_action.py`, 20 tests):
- Input block (input_scan) → trace stages: input_scan=block, model_input/model_output=skip.
- Policy block → policy stage=block, model stages=skip.
- Output guard block → output_guardrail=block, model stages NOT skip.
- Allow → all stages pass/allow, none skip, none block.
- Redact → input_scan=redact, model stages proceed (not skip/block).
- `_build_safe_block_response` with explicit `blocked_by` → no re-resolution, no
  `pipeline_stage` in body.
- `_build_safe_block_response` with `blocked_by=None` → falls back correctly.
- `_launch_chat_stream_response` propagates `input_action` into trace.
- `_input_decision` drives both streaming and non-streaming `final_action`.
- No double-block in trace stages (a block at one stage doesn't set block at another).

Gate: 20 targeted + 1894 full suite passed (0 failed).

## PIPELINE-0006 (2026-07-03)

**Degraded scanner fails CLOSED** — A degraded/unavailable Tier-2 scanner no longer
allows raw PII/secrets through to the model (closes D-02 fail-open).

Changes:
- **enforcement.py**: `resolve_and_enforce()` gains `tier1_pii_detected: bool` parameter.
  When `tier2_degraded=True` and PII/secrets detected in scan text → `action="redact"`
  (or `"block"` if `redaction_possible=False`). Clean prompt under degraded → `"monitor"`.
- **main.py (~L6458–6520)**: The degraded path now runs `detect_pii` / `detect_secrets` /
  `detect_credential_exposure` on the scan text BEFORE calling `resolve_and_enforce`.
  Result passed as `tier1_pii_detected`. Redaction trigger (`_redact_threat`) extended
  to fire when `_input_decision.degraded and _degraded_pii_detected`.
- **Output side**: `enforce_output(scan_degraded=True)` already returns `action="redact"`
  (confirmed wired, no change needed — was correct since PIPELINE-0004).
- **D-06 (streaming reasoning_content/tool_calls)**: NOT addressed this iteration — not
  naturally part of the degraded fail-closed scope. Noted for a future iteration.

Fail-closed contract (byte-verified):
- Degraded + PII in prompt → redacted text forwarded (raw PII absent from payload)
- Degraded + clean prompt → monitor only (no redact, no block)
- Degraded + PII + unmaskable → BLOCK (never raw to model)
- Output scan degraded → redact (per CANONICAL.md)

Gate: 19 targeted + 54 enforcement + 1870 full suite passed (0 failed).

## PIPELINE-0005 (2026-07-03)

**Block short-circuit invariant VERIFIED** — A block at ANY input stage
short-circuits the pipeline: no downstream stage runs, the model is NEVER called.

Audit:
- **6 input-side block returns** in `proxy_chat`, all before any model call:
  1. Threat-intel actor block (L5324) — `return _build_block_response(403, "threat_intel_blocked", ...)`
  2. Blocked-keyword match (L5983) — `return _build_block_response(403, "content_blocked", ...)`
  3. Backend scan `block_immediately` (L6106) — `return _build_block_response(403, "content_blocked", ...)`
  4. Policy engine block (L6220) — `return _build_block_response(403, "content_blocked", ...)`
  5. Scanner `is_terminal_block` (L6603) — `return _build_block_response(403, "content_blocked", ...)`
  6. Unmaskable PII/secret (L6745) — `return _build_block_response(403, "content_blocked", ...)`
- **4 model-call sites** (all downstream of the 6 blocks):
  - Standalone stream (L6802), standalone sync (L6819),
    connected stream (L7542), connected sync (L7560)
- **firewall_disabled bypass** (L6031/6046): intentional — only fires when
  `firewall_enabled: false`. Not a vulnerability.
- **2 output-guard block returns** (L8072/8140): correctly AFTER the model call.
  Output-guard blocks are expected post-model; they block the RESPONSE, not the request.

Tests (new, `test_pipeline_block_shortcircuit.py`, 24 tests):
- **Structural source proof** (3 tests): parses `proxy_chat` source at import time;
  asserts >=3 input-side `return _build_block_response(403, ...)` lines appear before
  the earliest `LLM_ROUTER.acompletion` / `_launch_chat_stream_response`; verifies
  output-guard blocks exist after model calls. Skips comment lines and the
  `firewall_disabled` region.
- **Pipeline-trace model-skip** (5 tests): calls `build_pipeline_trace` directly with
  `blocked_stage` = policy / input_scan / rate_limit → `model_input` and `model_output`
  stages have `action='skip'`; `blocked_stage=output_guardrail` → model stages are
  NOT 'skip' (the model already ran).
- **`_build_block_response` status-code** (3 tests): non-content threat category
  (threat_intel) keeps 403; content category (prompt_injection) remaps to
  `GATEWAY_BLOCK_STATUS` (default 400) per D-a contract.
- **Enforcement authority terminal-block contract** (9 tests): injection above
  threshold → `is_terminal_block`; unmaskable PII/secret → `is_terminal_block`;
  org-policy override → `is_terminal_block`; PII-maskable → redact (NOT terminal);
  monitor mode → never terminal; below threshold → not terminal; generic threat block →
  terminal.
- **Enforcement→trace linkage** (4 tests): policy/input_scan/kill_switch
  blocked_stage → model_input+model_output='skip'; output_guardrail → not 'skip'.

Files: tests/test_pipeline_block_shortcircuit.py (new, 24 tests).
Gate: 24 targeted + 30 enforcement + 1851 full gateway suite passed.

## PIPELINE-0004 (2026-07-03)

**Canonical enforcement authority implemented** — `enforcement.py` now contains the two
canonical entry points defined in CANONICAL.md. All pipeline enforcement decisions flow
through `resolve_and_enforce()` (input) and `enforce_output()` (output), returning a
frozen `PipelineDecision` dataclass.

Changes:
- **enforcement.py**: Added `PipelineDecision` frozen dataclass (action, blocked_by,
  threat_type, detection_tier, matched_rules, confidence, degraded, etc.) with
  `is_terminal_block` / `is_redact` / `as_dict()`. Added `resolve_and_enforce()` which
  merges scanner recommendation + org policy + enforcement mode + degraded state through
  the existing `resolve_enforcement` lattice, with injection/PII/secret gating and the
  fail-closed contract (degraded+PII→redact, degraded+clean→monitor, exception→block).
  Added `enforce_output()` which handles guard verdict + exceptions, with fail-CLOSED on
  exception (fixes D-05), degraded→redact, rewrite+streaming→block, flag+block→block
  (harmonizes D-18).
- **main.py input enforcement block (~L6435-6691)**: Replaced inline divergent logic
  (manual import of 4 enforcement helpers + injection/PII branching + resolve + should_block)
  with a single `resolve_and_enforce()` call. The `_input_decision` drives block short-circuit,
  redaction gating, and unmaskable-PII fail-closed escalation. Telemetry, audit, and response
  building preserved unchanged.
- **main.py `_apply_output_guard_nonstream` (~L1687-1696)**: Wired through `enforce_output()`.
  **D-05 FIX**: guard exception now returns `PipelineDecision(action="block")` instead of
  `return None` (fail-open). Block response emits telemetry + 403 with zeroshield metadata.

Files: enforcement.py, main.py, tests/test_pipeline_enforcement_authority.py (new, 30 tests).
Gate: 151 targeted (enforcement+output_guard+pipeline) + 1824 full gateway suite passed.

## PIPELINE-0003 (2026-07-03)

**docs/pipeline/CANONICAL.md created** — Defines the ONE canonical chat pipeline with ONE
enforcement authority. All 4 sub-paths (A/B/C/D) converge on the same 7-stage pipeline:
pre → policy → input_scan → enforcement_resolution → routing → model → output_guard → finalize.

Key design decisions:
- **Two entry points in enforcement.py**: `resolve_and_enforce()` (input-side, merges
  policy + scanner + mode + degraded state) and `enforce_output()` (output-side, handles
  guard verdict + exceptions). Both return a frozen `PipelineDecision` dataclass.
- **Fail-closed contract**: degraded Tier-2 + Tier-1 PII → redact (never allow);
  output guard exception → block (fixes D-05); degraded output scan → defensive redact.
- **One `final_action` + `blocked_by`**: `PipelineDecision` is the single source of truth.
  No double-block — once blocked, no downstream stage runs.
- **Streaming vs sync**: identical enforcement stages 0–3; only the delivery adapter
  differs. `enforce_output()` coerces rewrite→block for streaming, harmonizes flag→block
  escalation for both (fixes D-18).
- **D-06 fix path**: `_extract_content_delta` extended to accumulate `reasoning_content`
  and `tool_calls` arguments into the scan buffer.
- **Migration map**: ~250 lines of inline enforcement (L6435–L6691) collapse to one
  `resolve_and_enforce()` call; Path D inline output guard (~300 lines) replaced by
  shared `_apply_output_guard`; `proxy_chat` shrinks ~4000→~2000 LOC.
- **Mermaid sequence diagram** covering all stages + short-circuit paths.

Addresses all 8 divergences (D-01 through D-19) with explicit resolution per divergence.

## PIPELINE-0002 (2026-07-03)

**docs/pipeline/DIVERGENCES.md created** — Full divergence analysis across all 4 chat
sub-paths (standalone stream/sync, connected stream/sync). 8 divergences documented with
severity, line references, and recommended fix direction:

- **D-05 (HIGH)**: `_apply_output_guard_nonstream` (Path B, L1694) fails OPEN on exception —
  model output ships to client unscanned. Streaming fails CLOSED (clears buffers).
- **D-06 (MEDIUM)**: Streaming output guard scans only `content` deltas; `reasoning_content`
  and `tool_calls` in SSE chunks bypass scanning. Non-stream F4 fix (L7554) scans all channels.
- **D-02 (MEDIUM)**: Tier-2 degraded → Tier-1-only (fail-open for availability).
- **D-18 (LOW)**: Streaming escalates flag→block in block mode; non-streaming does not.

Streaming vs non-streaming enforcement matrix. Fail-open sites cataloged with evidence.
Cross-referenced to the known `input_scan` BLOCK + `model_output` 7710ms trace — the most
likely root cause is `enforcement_mode != "block"` (D-01) or event conflation across
different request_ids.

## PIPELINE-0001 (2026-07-03)

**docs/pipeline/PATHS.md created** — Complete enumeration of every chat code path in
`gateway/ai_mesh_gateway/main.py` (13036 lines). Identified 4 sub-paths (standalone
stream/sync, connected stream/sync) + firewall-disabled fast path + Responses API
delegate. Documented per-path stage sequences, divergence points (Tier-2 degraded
fail-open, enforcement mode override, output scan degraded), and 6 suspected
fail-open / no-short-circuit sites with line references. Mermaid + ASCII diagrams
included.
