# CHG-0119 — ext-proxy did not credential-scan completion/complete CLIENT INPUT before egress

**Change-id:** CHG-0119
**Date:** 2026-07-03
**Severity:** MEDIUM (a credential/secret in the user's completion input would egress to an untrusted third-party external server unscanned).
**Area:** HARDEN 1.4 — context minimization / no credential egress to external servers (input-side parity for CHG-0118).
**Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`ext_mcp_proxy` inbound scan); `gateway/ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py` (+2 tests).
**Whose work it touches:** the owning-session `ext_mcp_proxy` inbound scan (input-side twin of CHG-0118; extends CHG-0041 arg-scan / CHG-0109 inbound-redact audit).

## Root cause

CHG-0118 added `completion/complete` to the ext-proxy RESULT scan, but its CLIENT INPUT was still forwarded to the
external server unscanned. The ext-proxy inbound credential-scan only fires for `_EXT_ARG_SCAN_METHODS`
(tools/call, prompts/get) and only inspects `params.arguments`:

```python
if _ext_req.get("method") in _EXT_ARG_SCAN_METHODS:
    _ext_args = _ext_req["params"].get("arguments")   # <-- only this shape
    if _ext_args is not None: scan + block credential
```

`completion/complete`'s client input has a DIFFERENT shape — `params.argument.value` (the partial value the user is
typing) + `params.context.arguments` (prior arg values) — so it was never scanned. A credential/secret in that input
egressed to the untrusted (allowlisted) external server. Asymmetric: the completion RESULT was scanned (CHG-0118),
but the completion INPUT was not (unlike tools/call, which scans both directions).

## The fix (CHG-0119)

`ext_mcp_proxy` now extracts `completion/complete`'s `params.argument.value` + `params.context.arguments` into a
synthetic input dict and runs it through `_scan_tool_args_block` (the same E12 credential force-block + inbound-redact
machinery as tools/call args) BEFORE egress:
- a credential → **BLOCKED** (JSON-RPC compliance error, `client.send` never awaited; audited
  `credential_blocked_inbound`).
- an inbound redaction → masked values written back into `params.argument.value` / `params.context.arguments` +
  re-serialized body + audited (`pii_redacted_inbound`).
- benign input → forwarded unchanged.

Input + output of `completion/complete` are now both scanned (parity with tools/call).

### Byte-level truth

- completion `argument.value` `"my key AKIAIOSFODNN7EXAMPLE"` → request BLOCKED, `AKIAIOSFODNN7EXAMPLE` never reaches
  `client.send` (`client.send.assert_not_awaited()`).
- credential in `context.arguments.prev` → BLOCKED, not forwarded.
- benign `argument.value` `"get"` → forwarded (`client.send.assert_awaited()`), completion values still result-scanned.

## Verification (all green)

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py -q -k "completion or benign_completion"   # 4 passed
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                                                    # 1720 passed, 0 failed
```

Broker unaffected (gateway-only change). Drives the REAL `ext_mcp_proxy`.

## Scope / honesty note

Input-side parity for CHG-0118; the completion method is now scanned both directions. Consistent with the existing
ext arg-scan design (enabled_info=None → "tag" posture → only CREDENTIALS force-block; generic PII in client input
under "tag" is not redacted on the transport-level ext path, same as tools/call args). **Oracle:** N/A — aidefence is
blind to the AWS-key class (documented in prior findings), so the secret-not-egressed + `client.send`-not-awaited byte
assertions are authoritative. Does not change the host-blocked live-stress status (items 14–20). Partial coverage is
not completion.
