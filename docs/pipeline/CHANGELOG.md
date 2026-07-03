# Pipeline Changelog

All changes to the chat pipeline consolidation/fix/freeze effort.

## PIPELINE-0017 (2026-07-03)

**Latency breakdown + actionable reduction hints on pipeline trace (P5 item 17).**

Root Cause:
- Scan Detail showed per-stage latency bars and total duration (PIPELINE-0016) but gave
  operators no dominant-stage attribution or guidance on how to reduce end-to-end latency
  (e.g. model_output 7710ms with no "use a faster model / enable caching" hint).

Fix:
- `pipeline_trace.py`: `build_latency_breakdown()` + `attach_latency_breakdown()` attach
  `latency_breakdown` to every trace (dominant stage/share, sorted `by_stage`, `hints[]`
  with severity + actions). Stage-specific hints: model_output → faster model/caching;
  input_scan → Tier-2 async; policy → rule count/bundle cache; overhead secondary hint.
- `stream_orchestration.py`: recompute breakdown after stream total reconciliation.
- `frontend/src/utils/pipelineTrace.js`: extend `resolveLatencyBreakdown` / add
  `resolveLatencyHints` with legacy-trace fallback (derive stage sum from `stages[]`).
- `LogDetailPage.jsx`: Latency Breakdown table + "How to reduce latency" hint cards.

Verification:
- 8 new gateway tests (`test_pipeline_latency_breakdown.py`).
- 5 new frontend tests (`pipelineTrace.test.js` breakdown/hints/legacy).
- Browser (:8180): event 295909 → Duration 14860.9ms, hint card visible, model-output
  reduction actions present. Evidence: `mcp-parallel/findings/pipeline-p17-latency-hints/`.
- Gate: breakdown 8 + latency 5 + pipelineTrace 18 passed; lint + build green.

## PIPELINE-0016 (2026-07-03)

**Frontend Duration/total matches backend latency; streaming TTFT exposed (P5 item 16).**

Root Cause:
- `LogDetailPage` and module log charts used `meta.latency_ms` (often `0` on streamed
  events) instead of `pipeline_trace.total_latency_ms` from PIPELINE-0015 — UI showed
  `0ms` while backend had e.g. `13555ms`.
- `liveGateway.js` fallback path preferred client `context.totalLatencyMs` over
  `pipeline_trace.total_latency_ms`; streaming SSE ignored the M-51 terminal trace
  frame carrying the authoritative trace.
- Streaming terminal `pipeline_trace` was built at stream launch (stale totals); `ttft_ms`
  was emitted only in telemetry metadata, not in the client-visible trace frame.

Fix:
- `frontend/src/utils/pipelineTrace.js`: `resolveTotalLatencyMs`, `formatPipelineDurationMs`,
  `resolveTtftMs`, `resolveLatencyBreakdown` (prefers `total_latency_ms` → sum+overhead).
- `LogDetailPage.jsx`, `module-specific-log-charts.jsx`, `liveGateway.js`: wire Duration/total
  to pipeline trace; SSE `terminalTracePayload` capture; hoist `stage_latency_sum_ms`,
  `overhead_ms`, `ttft_ms`.
- `AttackSimulatorPanel.jsx`, `SimulatorShell.jsx`: show `TTFT Nms` on stream results.
- `stream_orchestration.py` `build_stream_trace_frame`: reconcile `total_latency_ms` on
  completed stream wall-clock; expose `ttft_ms` on zeroshield + pipeline_trace.

Verification:
- 5 new frontend tests (`pipelineTrace.test.js` latency/ttft helpers).
- 1 new gateway test (`test_build_stream_trace_frame_reconciles_total_and_ttft`).
- Browser live (:8180): Scan Detail `Duration: 13607.1ms` / `Total Duration: 13607.1ms`
  (not `0ms`); stage latencies visible (model_output 7757.1ms).
- Gate: frontend lint + build green; pipelineTrace 13/13; stream+latency gateway 6/6.

Evidence: `mcp-parallel/findings/pipeline-p16-frontend-latency-ttft/` (browser + unit tests)

## PIPELINE-0015 (2026-07-03)

**Per-stage latency instrumentation + reconciliation (P5 item 15 / L8).**

Root Cause:
- `build_pipeline_trace()` `_latency()` used hardcoded fake defaults (0.1–0.5 ms) for
  unrouted stages and fell back to `zeroshield.processing_time_ms` (wall-clock) for
  `input_scan` / `model_output` — so a blocked request showed `model_output` ≈11.6 ms
  (total request time) even when the LLM never ran.
- `total_latency_ms` was overridden by `processing_time_ms` instead of
  `sum(stages) + overhead_ms`.

Fix:
- `pipeline_trace.py`: `PipelineStageTimer` (monotonic `perf_counter`) +
  `finalize_stage_metrics()`; measured keys for all 9 stages; skipped stages forced
  to `latency_ms=0`; `total_latency_ms = stage_latency_sum_ms + overhead_ms` (explicit
  fields on trace).
- `main.py` `proxy_chat`: perf_counter boundaries for auth, kill_switch, rate_limit,
  policy (existing), input_scan tiers (existing), model_routing, model_input prep,
  model_output/upstream, output_guardrail; finalize before `build_pipeline_trace`.

Verification:
- 5 new tests (`test_pipeline_latency.py`): skip-zero-latency, sum invariant, no fakes,
  upstream-not-processing_time, timer unit.
- Gate: 2039 gateway tests passed.

Evidence: `mcp-parallel/findings/pipeline-p15-latency-reconciliation/` (tests authoritative)

## PIPELINE-0014 (2026-07-03)

**Output guard REDACTS maskable PII (not block); byte-verified; noop scrub fail-closed (P4 item 14).**

Root Cause:
- Connected sync Path D output-guard block (~L7858+) branched on raw
  `output_verdict.action` instead of canonical `enforce_output()` — PII could
  hard-block when org posture is redact; noop scrub downgraded to `flag` and
  delivered raw bytes.
- `enforce_output()` lacked input-path parity: maskable `block` verdict not
  downgraded to `redact`; no `redaction_possible` → block fail-closed contract.
- `_apply_output_guard_nonstream` computed `_out_decision` but still gated block/
  redact on raw `verdict.action`.

Fix:
- `enforcement.py`: `enforce_output()` gains `redaction_possible` +
  `pii_detection_enabled`; maskable PII/secret/credential `block` → `redact`;
  `redact` + `redaction_possible=False` → `block` (noop fail-closed).
- `main.py`: connected sync output block wired through `enforce_output()`;
  redact path byte-checks sanitize → blocks on noop; defensive `redact_all` when
  `scan_degraded`; `_apply_output_guard_nonstream` uses `_out_decision` throughout.

Verification:
- 13 new tests (`test_pipeline_output_redact.py`) + 2 enforce_output contract updates.
- Gate: 2029 gateway tests passed (4 pre-existing MCP internal-route failures unrelated).

Evidence: `mcp-parallel/findings/pipeline-p14-output-redact/`

## PIPELINE-0013 (2026-07-03)

**Output guard scans model-generated text only; empty output is not PII (L6/L7).**

Root Cause:
- **L7:** Whitespace-only completions (`"   \n\t  "`) were truthy scan text → output
  guard ran PII/secret checks on non-substantive “empty” model output.
- **L6:** `sync_pre_llm` built `pipeline_trace` before `_apply_output_guard_nonstream`,
  and `output_guardrail.detail` fell back to input-side `zeroshield.reason` → operator
  trace showed input redaction text on the output stage when model output was empty.
- Streaming `_flush_buffer` did not normalize whitespace-only buffered text before
  `OUTPUT_GUARD.inspect()`.

Fix:
- `output_guard.py`: `normalize_output_scan_text()` — whitespace-only → empty; `inspect()`
  returns allow on empty scan text.
- `main.py`: `_model_output_scan_text()` wraps `_extract_scannable_output_text` +
  normalization; used by Path B + Path D; `sync_pre_llm` runs output guard before trace
  and passes `output_scan_verdict` into `build_pipeline_trace`.
- `pipeline_trace.py`: output_guardrail `detail` no longer falls back to input-side
  `zs.reason` unless `detection_tier=="output_guard"`.
- `secure_streaming.py`: normalize buffered text before guard scan; skip guard on empty.

Verification:
- 12 new tests (`test_pipeline_output_guard_model_only.py`).
- Gate: 2022 gateway tests passed.

Evidence: `mcp-parallel/findings/pipeline-p13-output-guard-model-only/`

## PIPELINE-0012 (2026-07-03)

**Pre-masked smart-mask PII must REDACT-forward, not BLOCK at input_scan.**

Root Cause:
- User prompt carried policy-style partial masks (`j***@a***.com`, `***-**-6789`,
  `***-***-5309`, `****-****-****-1111`). Policy stage ALLOW (no rules matched).
  Input scan blocked with `obfuscated_pii` / later `pii` + B1 unmaskable.
- **G53 false positive (PIPELINE-0011 insufficient):** `_MD_EMPH_INTERLEAVE` stripped
  3+ asterisk smart-mask runs as markdown emphasis (`j***@a***.com` → `j@a.com`).
  G53 then saw email in stripped text absent from raw → `obfuscated_pii` BLOCK.
- **B1 honesty false positive:** Scanner correctly returned `redact`, but
  `redact_all` is a no-op on already-masked bytes → `_redaction_noop` fail-closed
  BLOCK as "unmaskable".
- **Enforcement downgrade:** When org `scan_block_on_pii=false`, `resolve_and_enforce`
  downgraded scanner `redact` → `allow` even though smart-mask shapes were present.

Fix:
- `patterns.py`: smart-mask PII patterns (`email_smart_masked`, `ssn_smart_masked`,
  `phone_smart_masked`, `card_smart_masked`) with identity maskers; cap emphasis strip
  to `{1,2}` markers; `contains_smart_redaction_markers()` +
  `smart_mask_redaction_noop_is_expected()`.
- `scanner.py`: G53 skips obfuscated block when smart masks present and raw has no PII.
- `main.py`: B1 noop guard exempts smart-mask prompts; force PII redact eligibility
  when smart-mask shapes present.
- `pipeline_trace.py`: input_scan stage shows `redact` when final_action=redact on
  smart-mask no-op (bytes unchanged by design).

Verification:
- 7 new tests (`test_pipeline_obfuscation_fp.py` + `test_pipeline_pre_masked_redact.py`).
- LIVE curl: `input_scan` enforcement REDACT, masked prompt forwarded to model (not 403).
- Gate: 2009 gateway tests passed.

Evidence: `mcp-parallel/findings/pipeline-p12-live-proof.json`

## PIPELINE-0011 (2026-07-03)

**Tier-1 false positive fixed (L3): plain-text PII no longer classified as
"Markdown/HTML-obfuscated".**

Root Cause:
- The G33 (text-encoding), G53 (markdown-emphasis), and G76 (MCP encoded-exfil)
  obfuscation detectors fire when *any* decoded/stripped variant reveals
  PII/secret that the original text didn't contain. The intended contract: only
  classify as "obfuscated" if the detection was genuinely REVEALED by decoding —
  i.e., the pattern was hidden by encoding/markup and only became detectable
  after stripping.
- Bug: all four `detect_*` functions (`detect_pii`, `detect_secrets`,
  `detect_credential_exposure`, `detect_ip_leakage`) internally canonicalize
  via `canonicalize_for_detection()` (strips zero-width characters, folds
  fullwidth, etc.). The G33/G53/G76 guards compared decoded-variant detections
  against "raw-text detections" to filter out kinds already present in plain
  text — but the "raw" detections used the canonicalizing `detect_*` functions.
  This caused two classes of false positive:
  1. **Plain PII + unrelated markup**: A sentence with a plain SSN and an
     unrelated `**bold**` word or `&#169;` entity. `strip_interleaved_emphasis`
     removes the emphasis markup → `detect_pii` finds the SSN (which was
     already in plain text). The guard `_md_stripped != _msrc` fires because
     emphasis WAS stripped somewhere, and since `detect_pii(stripped)` found the
     SSN, it was wrongly classified as "obfuscated" → block instead of redact.
  2. **ZWC-obfuscated secrets (correctness)**: Conversely, a ZWC-interleaved
     credential (e.g., `sk\u200b-ant\u200b-...`) was detected by `detect_secrets(raw)`
     via internal canonicalization → appeared in `_raw_detected_kinds` → filtered
     out of `_hidden` → lost the genuine obfuscation-block.

Fix:
- Replace all four `detect_*` calls in the plain-text filter with their `_*_core`
  counterparts (`_detect_pii_core`, `_detect_secrets_core`,
  `_detect_credential_exposure_core`, `_detect_ip_leakage_core`) which perform
  NO internal canonicalization. These accurately report what is detectable in
  the truly raw text without any deobfuscation.
- Applied to three sites: `scanner._scan_prompt_sync` G33 + G53 blocks,
  `mcp_scan_orchestrator._scan_text_tier1_sync` G76 block.
- Net effect: plain-text PII with unrelated markup → "pii" threat_type (redact),
  never "obfuscated_pii" (block). Genuinely obfuscated content (ZWC, HTML-entity,
  markdown-emphasis split) → still correctly blocked.

Tests:
- New `test_pipeline_obfuscation_fp.py` — 17 test cases:
  - 6 plain-PII-with-emphasis/entity → action=redact, threat_type=pii (not
    obfuscated_pii)
  - 4 genuinely obfuscated → action=block, threat_type=obfuscated_*
  - 2 MCP orchestrator (plain secret + entity vs genuinely encoded secret)
  - 5 benign markdown/entity → action=allow

Gate: 17 targeted + 2000 full suite passed, 0 failed. No regression on
CHG-0076/0079/0083 (74 obfuscation/unicode/tools-list-audit tests all green).

## PIPELINE-0010 (2026-07-03)

**resolve_enforcement REDACT mapping fixed (L5).**

Root Cause:
- `resolve_enforcement()` in `enforcement.py` has a "monitor posture" rule at
  L134-136: when `resolved == "block"` and `enforcement_mode != "block"`, it
  downgrades the action to `"monitor"`.
- Bug 1: When the scanner recommended `"redact"` (PII masking) but the org
  policy was `"block"` and enforcement_mode was not `"block"` (e.g. "monitor"),
  `max_action` returned `"block"` (from policy), and the monitor-posture rule
  downgraded it to `"monitor"` — losing the PII masking intent entirely. While
  `resolve_and_enforce` compensated via `should_apply_redaction("monitor","pii")`
  → True, the atomic `resolve_enforcement` function violated its own REDACT
  contract (exported public API, could be called independently).
- Bug 2: For unmaskable PII (`redaction_possible=False`) with `org_policy_action
  ="block"` under non-block `enforcement_mode`: L123 didn't fire (resolved was
  already "block" from max_action, not "redact"), L125 didn't fire (policy IS
  "block"), and L134 downgraded to "monitor". In `resolve_and_enforce`, the
  resulting `PipelineDecision(action="redact")` (via `should_apply_redaction`)
  had `is_terminal_block=False`. The main.py honesty check at L6805 requires
  `is_terminal_block=True` to block → unmaskable PII leaked to the model under
  non-block enforcement modes.

Fix:
- The monitor-posture downgrade (L134-136) now branches three ways:
  1. `rec == "redact" and not redaction_possible` → **no downgrade** (fail-closed
     block overrides monitor posture; data-protection last-resort).
  2. `rec == "redact" and redaction_possible` → `resolved = "redact"` (preserves
     PII masking; the monitor posture suppresses blocks, not redactions).
  3. Otherwise → `resolved = "monitor"` (unchanged for injection/other threats).

Contract:
- REDACT recommendation → REDACT under any posture when bytes can be masked.
- Unmaskable PII always fails closed to BLOCK regardless of enforcement_mode.
- Injection/other threats still respect monitor posture (block → monitor).
- `resolve_and_enforce` produces correct `PipelineDecision` without relying
  on the `should_apply_redaction` override for this case (defense-in-depth).

Tests: 17 new tests in `test_enforcement.py` (7 atomic resolve_enforcement +
10 end-to-end resolve_and_enforce scenarios). Gate: 28 targeted + 1965 full
gateway suite passed.

## PIPELINE-0009 (2026-07-03)

**Policy REDACTS PII/PCI/PHI before input_scan (B-POL fix).**

Root Cause:
- `policy_engine.evaluate()` resolves the MAX action across all matched rules.
  When both redact AND block rules co-match (e.g. PCI: PAN redact + CVV block,
  or PII: email redact + a secrets block rule), the final action is "block".
- `_policy_check_cached` in `main.py` only applied `redaction_hints` when
  `result.action == "redact"`, silently discarding them when action="block".
- Result: the caller received action="block" with NO redacted_prompt → hard
  block at L6237 without ever masking the PII/PCI content. The scanner stage
  never saw the prompt (blocked before scan_text assignment).

Fix:
- When `result.action == "block"` AND `result.redaction_hints` is non-empty,
  `_policy_check_cached` now:
  1. Applies all redaction hints (masking PII/PCI/PHI content).
  2. Re-evaluates ONLY block rules against the post-redaction text.
  3. If NO block rule still matches → downgrades action to "redact" and returns
     the masked prompt (pipeline continues through input_scan with clean text).
  4. If a block rule STILL matches → keeps action="block" (genuinely unmaskable
     dangerous content) but still provides the redacted_prompt for metadata.

Contract preserved:
- Compile+push path verified correct (control compiler emits redaction_config
  with replacement; POLICY_SYNC delivers bundles to gateway policy engine).
- `proxy_chat` L6306-6317: action="redact" + redacted_prompt → effective_prompt
  updated → scan_text = effective_prompt → scanner sees masked text only.
- `resolve_and_enforce` at the scanner stage still downgrades PII/secret blocks
  to redact (unchanged behavior for the scanner path itself).

Gate: 18 new tests (test_pipeline_policy_redact.py) + 1948 full gateway suite passed.

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
