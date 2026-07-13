# Phase 1 — Chat output-guard architecture map

Source: explore agent (Map chat output guard). Persisted 2026-07-10.

## Authority split (critical)

| Path | Enforcement |
|------|-------------|
| Non-stream (B/D) | `OutputGuard.inspect` → **`enforce_output`** |
| Stream (A/C) | `OutputGuard.inspect` → **inline coerce in `SecureStreamingResponse._flush_buffer`** — does **not** call `enforce_output` |

## New findings to live-prove

| ID | Issue |
|----|--------|
| F-010 | Stream redact-noop → deliver unchanged as `flag` (non-stream → block) |
| F-011 | Stream tier-2 degraded may release after static; non-stream `scan_degraded`→redact |
| F-012 | `output_policy_*` post-LLM on Path D; streaming policy wiring unverified |
| F-007 confirmed | `output_exfil_*` not in Redis `build_gateway_payload` / config_sync lists |

Full detector/action/config tables: agent transcript (chat map). Test inventory listed there.
