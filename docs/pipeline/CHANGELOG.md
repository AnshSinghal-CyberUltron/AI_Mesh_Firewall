# Pipeline Changelog

All changes to the chat pipeline consolidation/fix/freeze effort.

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
