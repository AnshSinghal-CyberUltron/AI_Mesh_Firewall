# CHG-0093 — SSE multi-line `data:` split evades the MCP tool-result scanner

**Change-id:** CHG-0093
**Date:** 2026-07-03
**Severity:** HIGH — fail-open 1.4 leak on the SSE tool-result path; an untrusted upstream can smuggle a secret/PII past the outbound scanner via a spec-valid framing trick.
**Area:** HARDEN 1.4 — field-level redaction of tool RESULTS (byte-verified, fail-closed), streamable-http/SSE transport.
**Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`_scan_reframe_sse_tool_result`); `gateway/ai_mesh_gateway/tests/test_mcp_sse_multiline_split.py` (new).
**Whose work it touches:** the owning-session proxy (`mcp_proxy.py`); hardens the SSE result-scan floor added in CHG-0039/0043/0064/0070.

## Root cause

Per the WHATWG SSE spec, a single event's data is the concatenation of ALL its `data:` field values joined by `"\n"`. `_scan_reframe_sse_tool_result` walked the buffered SSE **line-by-line** and parsed **each** `data:` line as standalone JSON (`json.loads(data_str)`). An untrusted upstream MCP server can therefore SPLIT its JSON-RPC result across several `data:` lines at a **structural** point (JSON whitespace between tokens):

```
data: {"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text",
data: "text":"leak AKIAIOSFODNN7EXAMPLE at 10.9.8.7"}]}}
```

Each `data:` fragment is invalid JSON on its own → the per-line `json.loads` raised → the frame fell through to "not JSON → pass verbatim" (unscanned). But a spec-compliant client reassembles the two `data:` values (joined by `\n`, which is valid JSON whitespace between tokens) into the **complete, valid** result — so the secret reached the client raw.

## Proof the leak was real (pre-fix)

Driving the REAL reframer:
- **single-line** result → secret masked (the normal case worked);
- **multi-line split** → `secret_in_reframed_bytes=True`, and a spec-compliant client reassembled the passed-through fragments into **valid JSON carrying the secret** (`client_parses_valid_json_with_secret=True`).

## The fix (CHG-0093)

Rewrote `_scan_reframe_sse_tool_result` to parse the buffered SSE **per event** (blank-line boundaries), reassembling every event's `data:` values with `\n` BEFORE `json.loads` + scanning via `_scan_tool_result_floor`. On redact, re-emit any non-`data:` field lines (`event:`/`id:`/comments) verbatim followed by the masked payload as a single `data:` line (json.dumps is newline-free); on an unmaskable survivor, fail CLOSED (withhold the whole result); clean/keep-alive/non-JSON events pass through verbatim (framing preserved). Covers both `result` and `error` frames.

## Verification (all green)

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_sse_multiline_split.py -q          # 7 passed
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                          # 1540 passed, 0 failed (+7 new)
cd ../services/mcp-broker && ./.venv/bin/python -m pytest tests -q -k "not websocket"          # 108 passed
```

7 new tests: single-line masked (regression), 2-line split masked, 3-line split + `event:` field masked, split ERROR frame masked, unmaskable-survivor → fail-closed, benign multi-line-split preserved unchanged, keep-alive/non-JSON pass-through. Each asserts on the reframed egress AND on what a spec-compliant SSE client reconstructs.

### Byte-level truth (client-reassembled view)

| | client reconstructs |
|---|---|
| RAW (pre-fix) | `…"text":"user carol.roe@corp.example ssn 555-66-7788"…` |
| **FIXED** | `…"text":"user c***@c***.example ssn ***-**-7788"…` |

Secret variant: split result carrying `AKIAIOSFODNN7EXAMPLE` + `10.9.8.7` → both masked; client cannot reconstruct the secret.

### Independent oracle (aidefence, decoupled from patterns.py)

`aidefence_has_pii` on the client-reassembled JSON: **false** on the fixed egress, **true** on the raw (pre-fix) egress → an independent detector confirms the fix genuinely masks the split-smuggled leak.

## Scope / honesty note

This closes an SSE-framing evasion of the tool-result floor with byte-level + independent-oracle proof and full-suite regression. It does NOT change the host-blocked status of the full 300–500-sandbox live stress (items 14–20). Partial coverage is not completion.
