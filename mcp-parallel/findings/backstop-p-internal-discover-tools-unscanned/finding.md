# CHG-0108 — internal tool-DISCOVERY route returned upstream tools/list metadata UNSCANNED

**Change-id:** CHG-0108
**Date:** 2026-07-03
**Severity:** MEDIUM–HIGH (1.4 metadata leak + tool-poisoning / indirect-injection surface — an UNTRUSTED upstream MCP server's tool metadata was synced into the catalog + shown to the LLM unredacted).
**Area:** HARDEN 1.4 — field-level redaction of tool RESULTS/metadata (byte-verified, fail-closed) + tool-poisoning defense, on the internal discovery/sync route.
**Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (new `_scan_internal_tools_list` + `internal_discover_tools` sandbox & direct-httpx return points); `gateway/ai_mesh_gateway/tests/test_mcp_internal_discover_tools_scan.py` (new, 5 tests).
**Whose work it touches:** the owning-session proxy `internal_discover_tools` (the backend tool-SYNC route); extends the CHG-0077/0079/0092 tool-metadata scanning to the discovery path.

## Root cause

`internal_discover_tools` — the `X-Gateway-Internal-Key` route the backend calls to SYNC an MCP server's tool
catalog (`tools/list`) — returned the upstream response **RAW** on BOTH transport paths:

```python
if _is_sandbox_routed(transport):
    return await _adapter_forward(...)          # sandbox (stdio/ws): raw adapter tools/list
...
if "text/event-stream" in content_type:
    ... return JSONResponse(content=json.loads(data_str))   # direct SSE: raw
else:
    return JSONResponse(content=tools_resp.json())          # direct JSON: raw
```

Tool DESCRIPTIONS / names / inputSchema come **LIVE from an untrusted upstream MCP server** and are synced into
the backend catalog and shown to the model. So a secret / PII / internal-IP (or a CHG-0076 encoded-exfil payload)
embedded in a tool description egressed to the backend/LLM **unredacted** on the discovery path — while every
OTHER tools/list egress scans metadata: `org_mcp_jsonrpc` tools/list (CHG-0077/0092), the REST `org_mcp_tools_list`
(CHG-0079), and the external proxy (`_EXT_FINITE_RESULT_METHODS`). A pure discovery-path parity gap — and the
MOST upstream one, since the poison enters the catalog at SYNC time (before any runtime scan).

### Byte-level truth (pre-fix) — driving the REAL `internal_discover_tools`

A tool description `Fetch a URL. Contact bob.jones@corp.example key AKIAIOSFODNN7EXAMPLE host 10.9.8.7`:
- **direct-httpx path:** secret + email + internal-IP all egressed **RAW**.
- **sandbox path:** same — egressed **RAW**.

## The fix (CHG-0108)

New helper `_scan_internal_tools_list(payload, …)` scans the discovered payload before returning it:
- **tools-shaped result** → reuse `_scanned_tools_list_response` (CHG-0077/0081): masks a maskable metadata leak;
  fail-closed **BLOCKS** poisoned / unmaskable metadata (e.g. an encoded-exfil payload); audits block/redact.
- **bare ERROR ENVELOPE** (a tools/list auth-failure error can echo a token/URL) → scan the whole payload via
  `_scan_tool_result_floor` (CHG-0092 parity) + audit.

Wired into all three `internal_discover_tools` return points (sandbox: buffer the adapter response then scan;
direct SSE; direct JSON). Fetches `enabled_info` so a server's `monitor` override is respected. `actor=None`
(descriptions are not actor-scoped — matches the org tools/list scan). Full parity across every tools/list path.

### Byte-level truth (post-fix)

- description → `Contact b***@c***.example key AKIA****MPLE host [INTERNAL_IPV4_REDACTED]` (direct AND sandbox)
- poisoned `box 10.0.0.5 served key /home/bob/.ssh/id_rsa` desc → `tools/list withheld` (fail-closed block; `id_rsa`/IP absent)
- bare error envelope `auth failed token AKIA… at 10.0.0.9` → token + IP masked
- benign `Fetches a web page and returns its text.` → preserved (no block/redact audit)

### Independent oracle (aidefence)

`aidefence_has_pii` on the tools/list description JSON: **true** on the raw (pre-fix) bytes, **false** on the
masked bytes.

## Verification (all green)

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_internal_discover_tools_scan.py -q   # 5 passed
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                            # 1668 passed, 0 failed
cd ../services/mcp-broker && ./.venv/bin/python -m pytest tests -q -k "not websocket"             # 120 passed (unaffected)
```

5 new tests drive the REAL `internal_discover_tools` on both paths: direct desc secret+PII+IP masked + redact
audited; sandbox desc masked + audited; poisoned/unmaskable → withheld + block audited; error-envelope secret
masked; benign preserved.

## Scope / honesty note

Closes a metadata-leak + tool-poisoning gap on the tool-sync origin path; tools/list scanning is now at full
parity across org-jsonrpc / REST / ext-proxy / internal-discovery. NOTE (documented, matches CHG-0077): the floor
masks secret/PII/internal-IP and blocks encoded-exfil/unmaskable metadata; free-text PROMPT-INJECTION phrasing in
a description (imperative instructions to the model) is a SEPARATE, narrower detection concern (`_INJECTION_KEYWORDS`)
tracked as a cross-cutting follow-up, not closed here. Does not change the host-blocked live-stress status (items
14–20). Partial coverage is not completion.
