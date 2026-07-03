# CHG-0118 — ext-proxy forwarded completion/complete + resources/templates/list results UNSCANNED

**Change-id:** CHG-0118
**Date:** 2026-07-03
**Severity:** MEDIUM (1.4 leak + tool-poisoning — server-controlled model/user-facing content egressed raw on the external proxy).
**Area:** HARDEN 1.4 — field-level redaction of tool RESULTS + tool-poisoning defense; extends the CHG-0077/0080 model-facing-metadata scan to the remaining finite methods.
**Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`_EXT_FINITE_RESULT_METHODS`); `gateway/ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py` (+3 tests).
**Whose work it touches:** the owning-session `ext_mcp_proxy` finite-result scan set (extends CHG-0077 tools/list-metadata / CHG-0080 initialize-instructions).

## Root cause

The transparent EXTERNAL proxy (`ext_mcp_proxy`) scans a result only when its method is in
`_EXT_FINITE_RESULT_METHODS` (the `_ext_scan_result = method in _EXT_FINITE_RESULT_METHODS` gate drives both the JSON
and SSE result-scan branches). Two finite, server-controlled, model/user-facing result methods were **missing** from
the set → forwarded **RAW**:

1. **`completion/complete`** → `result.completion.values[]` — the autocompletion strings the client shows to the
   user / feeds to the model. A secret / PII / exfil-beacon in a suggested value leaked.
2. **`resources/templates/list`** → `result.resourceTemplates[].{name, description, uriTemplate}` — server-controlled
   metadata, model-facing exactly like `resources/list` (which IS scanned).

Both are the same leak / tool-poisoning class as tool descriptions (CHG-0077) and initialize instructions (CHG-0080),
just for two MCP methods omitted from the scan set. Both results are finite → safe to buffer + scan.

### Byte-level truth (pre-fix)

A `completion.values` carrying `AKIAIOSFODNN7EXAMPLE` + `bob@corp.example`, and a template `description` carrying
`AKIA…` + `10.9.8.7`, egressed **raw** through the ext proxy (method not in the scan set).

## The fix (CHG-0118)

Added `"completion/complete"` and `"resources/templates/list"` to `_EXT_FINITE_RESULT_METHODS`. Their finite results
are now buffered + scanned via the same result floor as every other finite method — masking secret/PII/internal-IP,
fail-closed blocking on unmaskable/encoded-exfil metadata, render-leak neutralization, and the depth/block-count
caps — on both the JSON and the SSE branches. A benign completion/template result is preserved (strict no-op).

### Byte-level truth (post-fix)

- `completion.values` `["key AKIAIOSFODNN7EXAMPLE","contact bob@corp.example"]` → `["key AKIA****MPLE","contact b***@c***.example"]`.
- template `description` `admin key AKIA… host 10.9.8.7` → `admin key AKIA****MPLE host [INTERNAL_IPV4_REDACTED]`.
- benign `completion.values` `["get_weather","get_time"]` → unchanged.

### Independent oracle (aidefence)

`aidefence_has_pii` on the completion result JSON: **true** on the raw (pre-fix) values, **false** on the masked.

## Verification (all green)

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py -q -k "completion or templates_list or benign_completion"  # 3 passed
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py -q   # 55 passed
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                              # 1713 passed, 0 failed
```

Broker unaffected (gateway-only change). Drives the REAL `ext_mcp_proxy`: completion values secret+PII masked;
template description secret+IP masked; benign completion preserved.

## Scope / honesty note

Closes the last two finite server-controlled result methods on the external-proxy scan surface; ALL finite MCP
result methods (tools/call, tools/list, resources/list, resources/read, resources/templates/list, prompts/list,
prompts/get, initialize, completion/complete) are now scanned on the ext proxy. NOTE (matches CHG-0077): the floor
masks secret/PII/internal-IP and blocks encoded-exfil/unmaskable metadata; free-text PROMPT-INJECTION phrasing in a
completion value / template description is a separate narrower detection concern, not closed here. Does not change
the host-blocked live-stress status (items 14–20). Partial coverage is not completion.
