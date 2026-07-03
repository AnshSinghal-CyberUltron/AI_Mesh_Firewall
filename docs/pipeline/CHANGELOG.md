# Pipeline Changelog

All changes to the chat pipeline consolidation/fix/freeze effort.

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
