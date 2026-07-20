# CHG-0091 — MCP adapter (stdio/ws) ERROR-envelope tool-result leak: redaction DETECTED then DISCARDED

**Change-id:** CHG-0091
**Date:** 2026-07-03
**Severity:** HIGH — fail-open 1.4 data leak on a realistic transport path (default posture).
**Area:** HARDEN 1.4 — "field-level redaction of tool RESULTS (byte-verified, fail-closed)", stdio/websocket transport.
**Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`org_mcp_jsonrpc` adapter tools/call output-scan swap logic); `gateway/ai_mesh_gateway/tests/test_mcp_adapter_error_envelope_redaction.py` (new).
**Whose work it touches:** the owning-session proxy (`mcp_proxy.py`); extends the CHG-0074 / E12 result-redaction floor to the JSON-RPC error-envelope shape. Closes a residual CHG-0061 explicitly (mis)claimed was covered ("ORG path unaffected — same floor").

## Root cause

On the stdio/websocket ADAPTER tools/call path, the outbound scan target is:

```python
_scan_target = payload.get("result") if "result" in payload else payload   # mcp_proxy.py ~L3233
```

So a **bare JSON-RPC error envelope** — `{"jsonrpc":"2.0","id":N,"error":{"code":…,"message":…}}` with **no `result` key**, the standard response an MCP upstream returns on tool FAILURE — IS scanned whole, and a secret / PII / internal-IP inside `error.message` IS detected and tagged.

But all three output swap branches were gated on `"result" in payload`:
- the redact branch (`elif _scanned_out is not _scan_target and "result" in payload`),
- the redaction-**floor** *condition* (`elif "result" in payload and _mcp_redact_result_on_detect_enabled() …`),
- the floor swap (`elif _scanned_floor is not _scan_target: payload["result"] = …`).

For an error envelope (`"result" not in payload`) the redacted output was **computed then discarded**, and the RAW error egressed at `return adapter_resp`. Under the DEFAULT `tag` posture the first scan only *detects* (does not mask), so the **floor** is the operative masker — and its gate was exactly the one that excluded error envelopes.

The streamable-http path was **not** affected: it captures `result_content = data.get("result") or data.get("content")` and swaps `_scanned_content` back **unconditionally** (`if _scanned_content is not result_content`).

## Proof the leak was real (pre-fix)

Empirical probe of the two-tier scan on the error envelope `{"error":{"message":"connect failed: postgres://svc:ghp_REALLOOKINGSECRET1234@10.0.0.5:5432/prod …"}}`:
- tag-posture scan: `blocked=False changed=False tags=['INFRA','SECRET','SOC2'] findings=1` → **detected but unchanged**.
- floor (enforcement_override=redact): `changed=True` → password/AWS/IP all removed → the floor CAN mask it.
- but the adapter swap gated on `"result" in payload` → for this envelope the mask was discarded → **raw egress**.

## The fix (CHG-0091)

- Dropped the `"result" in payload` guard from the redaction-floor **condition** so the floor fires for error envelopes under the default `tag` posture.
- Both redact-swap branches now write the redacted output back to the WHOLE envelope when there is no `result` key (`payload = _scanned_out` / `_scanned_floor`), and keep the audited `reason` in sync with the masked `error.message` (so the audit store does not persist the raw secret either).
- Block / floor-block branches were already shape-agnostic (they build a fresh `[BLOCKED]` response) → an unmaskable survivor still fails CLOSED.

## Verification (all green)

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_adapter_error_envelope_redaction.py -q   # 5 passed
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                                # 1534 passed, 0 failed
cd ../services/mcp-broker && ./.venv/bin/python -m pytest tests -q -k "not websocket"                # 108 passed
```

The 5 new tests drive the REAL `org_mcp_jsonrpc` handler end-to-end (only `_adapter_forward` / config / audit faked): redacted-under-tag, benign-unchanged, flag-off-leaves-raw (proves the floor is the masker), monitor-wins, and unmaskable-survivor → fail-closed `[BLOCKED]` + `isError`.

### Byte-level truth (the only source of truth) — real handler egress

| | `error.message` egressed |
|---|---|
| **FIXED** (floor ON) | `connect failed: [CONNECTION_STRING_REDACTED] timed out` |
| RAW (floor OFF) | `connect failed: postgres://svc:ghp_REALLOOKINGSECRET1234ABCD@10.0.0.5:5432/prod timed out` |

### Independent oracle (aidefence — decoupled from the patterns.py regexes under test)

On an **email/SSN** error envelope (a PII class aidefence detects), driven through the same real handler:
- FIXED egress (`a***@c***.example`, `***-**-6789`) → `aidefence_has_pii` = **false**
- RAW egress (`alice.smith@corp.example`, `123-45-6789`) → `aidefence_has_pii` = **true**

→ an independent detector confirms the fix genuinely masks the leak (not merely an action label).

**Oracle honesty note:** aidefence rated BOTH the redacted and the raw *connection-string* egress "safe / no PII" — it is **blind to the github-token-in-connection-string / RFC1918-IP class**. For that class the byte-level assertion (raw substring present/absent) is the authoritative check; the email/SSN pair above is where aidefence acts as a true independent oracle.

## Residual (noted, not fixed here — scope)

The tools/**LIST** adapter fall-through (`org_mcp_jsonrpc` ~L2943 returns the raw `adapter_resp` when the payload is not tools-shaped, including an error) is the same class but far lower leak-probability (list-metadata errors rarely carry per-call secrets). Left for a future scoped pass.

## Scope / honesty note

This closes a specific transport-path 1.4 leak with byte-level + independent-oracle proof and full-suite regression. It does NOT change the host-blocked status of the full 300–500-sandbox live stress (items 14–20) — that remains owned by the live load harnesses. Partial coverage is not completion.
