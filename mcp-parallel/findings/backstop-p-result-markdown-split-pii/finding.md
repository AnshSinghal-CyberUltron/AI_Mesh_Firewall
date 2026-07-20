# CHG-0099 — markdown-split / encoded PII-secret in MCP tool results evaded the scanner

**Change-id:** CHG-0099
**Date:** 2026-07-03
**Severity:** HIGH (fail-open 1.4 leak — a render-time reconstruction that evaded the raw regexes).
**Area:** HARDEN 1.4 — prevent MCP data leakage (result egress); complete chat-output-guard parity.
**Files:** `gateway/ai_mesh_gateway/mcp_scan_orchestrator.py` (`_neutralize_render_leaks` + `_neutralize_exfil_deep` + the finding); `gateway/ai_mesh_gateway/tests/test_mcp_result_exfil_beacon_defang.py` (+9 tests).
**Whose work it touches:** the owning-session MCP scan core (orchestrator); extends CHG-0096/0097; reuses the chat guard's `neutralize_encoded_pii` + `neutralize_markdown_split_pii`.

## Root cause

The chat output guard (`output_guard.sanitize_output_for_verdict`) applies THREE render-leak neutralizers in order: `neutralize_exfil_channels` → `neutralize_encoded_pii` → `neutralize_markdown_split_pii` (G44). CHG-0096 wired only the FIRST into the MCP result path. So a PII/secret whose chars are interleaved with inline markdown emphasis / code / HTML markers (`1**2**3-45-6789`, `AKIA**IOSFODNN7**EXAMPLE`, `john`&#96;`@`&#96;`example.com`, `4111**-1111-1111-**1111`, `1&#50;3-45-6789`) evaded the raw regexes — **nothing was detected** (`tags=[]`) — yet a markdown/HTML client STRIPS the emphasis on render and reconstructs the sensitive value. A malicious upstream tool result could thus leak a secret/PII past the firewall to the rendering client.

Empirically confirmed pre-fix: `The SSN is 1**2**3-45-6789 exactly` → egressed verbatim (`tags=[]`); rendered → `The SSN is 123-45-6789 exactly` (SSN leaked).

## The fix (CHG-0099)

`_neutralize_render_leaks(text)` composes all three neutralizers in the same order as the chat sanitizer, and `_neutralize_exfil_deep` (the JSON-leaf walker from CHG-0097) now calls it on each leaf — so exfil beacons AND markdown-split/encoded PII nested in a JSON result field are neutralized. A masked run becomes `[PII_REDACTED]`; the finding drives the E12 floor under the default `tag` posture (via `_findings_have_exfil`, threat_type `exfil`, detail generalized to "render-time reconstruction leak"). Each neutralizer is a STRICT no-op on benign markdown (`**bold**`, `2*3`, `` `code` ``, `a_b_c`).

## Verification (all green)

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_result_exfil_beacon_defang.py -q   # 27 passed
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                          # 1594 passed, 0 failed
cd ../services/mcp-broker && ./.venv/bin/python -m pytest tests -q -k "not websocket"          # 108 passed
```

+9 tests: 5 markdown/encoded-split PII/secret (SSN, email, AWS key, CC, HTML-entity SSN) → `[PII_REDACTED]`, rendered form no longer leaks, `exfil` finding + floor fired; 4 benign markdown untouched (no `[PII_REDACTED]`, no finding). All CHG-0096/0097 exfil-beacon tests still pass (regression).

### Byte-level truth (egress + rendered)

| input | egress | rendered |
|---|---|---|
| `The SSN is 1**2**3-45-6789 exactly` | `The SSN is [PII_REDACTED] exactly` | no SSN |
| `key AKIA**IOSFODNN7**EXAMPLE here` | `key [PII_REDACTED] here` | no AWS key |
| `This is **important** text` (benign) | unchanged | unchanged |

### Independent oracle (aidefence, on the client-RENDERED view)

`aidefence_has_pii`: **true** on the rendered raw (`The SSN is 123-45-6789 exactly`), **false** on the rendered fixed (`The SSN is [PII_REDACTED] exactly`) — an independent detector confirms the fix prevents the render-time reconstruction.

## Scope / honesty note

Closes the markdown-split / encoded-PII render-leak on the MCP result path — the MCP result egress now has full parity with the chat output guard's render-leak defense (exfil beacons + encoded-PII + markdown-split PII). A separate probe found a related lower-confidence vector — a secret SPLIT ACROSS content-array items (each block sub-pattern, reconstructed only if a client concatenates blocks without a separator) — noted as a future item (client-concat-dependent, FP-sensitive). Does not change the host-blocked live-stress status (items 14–20). Partial coverage is not completion.
