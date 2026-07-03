# Pipeline Changelog

All changes to the chat pipeline consolidation/fix/freeze effort.

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
