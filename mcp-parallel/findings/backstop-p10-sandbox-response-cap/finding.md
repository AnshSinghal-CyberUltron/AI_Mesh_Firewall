# BACKSTOP hardening — sandbox agent JSON upstream-response cap made incremental (CHG-0066)

- **Item:** G3 item 10 ("resource limits ... mem ... + containment") / "resource bombs (mem) contained".
  Sandbox-agent counterpart of CHG-0064 (gateway response cap).
- **Change-id:** CHG-0066 (2026-07-02)
- **Type:** Mem-bomb containment (buffer-then-check anti-pattern) on the untrusted-upstream read.

## Gap
The per-tenant sandbox agent (`services/mcp-broker/sandbox-image/agent/upstream_manager.py`) dials the
UNTRUSTED upstream MCP server (streamable-http). For a JSON (non-SSE) response it did:
```
raw = await response.aread()              # buffers the WHOLE streaming body into memory
if len(raw) > _MAX_RESPONSE_BYTES:        # ...THEN checks the size
    raise UpstreamError(-32000, "upstream response too large")
```
`response` comes from `client.stream(...)`, so `aread()` buffers the entire (untrusted) stream into
memory BEFORE the size check. A malicious upstream returning a multi-GB body OOMs the sandbox agent up
to the sandbox's mem limit (killing + forcing a restart of that tenant's sandbox) instead of being
cleanly rejected at the 8 MiB ceiling. The SSE branch right below (`aiter_lines` + running `total`
check) was ALREADY correctly incremental — only the JSON branch used the buffer-then-check anti-pattern
(the same one CHG-0064 fixed on the gateway).

## Fix
`upstream_manager.py` (streamable-http JSON branch): read incrementally via `response.aiter_bytes()`,
accumulating into a running `_total` and raising `UpstreamError(-32000, "upstream response too large")`
the INSTANT it crosses `_MAX_RESPONSE_BYTES` — so the agent never buffers more than the ceiling from an
untrusted upstream. Parity with the SSE branch. Behaviour/error message unchanged for legitimate
responses.

## Verification
- `cd services/mcp-broker && ./.venv/bin/python -m pytest sandbox-image/agent/tests/test_upstream_proxy.py -q -k "not websocket"`
  → 11 passed (incl. 2 new: a JSON response over the cap → -32000 "upstream response too large"; a JSON
  response under the cap still returns the tools/list result via the incremental reader). The 3
  `websocket` tests HANG in this environment PRE-EXISTINGLY (a real ws connection / async-loop issue in
  the sandbox — the streamable-http + SSE tests all pass first, and this change touches only the
  streamable-http JSON branch, nothing websocket).

## Containment note
The sandbox runs with a mem limit (per CHG-0015: Memory=2GiB) so the pre-fix OOM was CONTAINED to the
single tenant's sandbox (auto-recovered on restart) — this fix reduces the blast radius from
"sandbox OOM + restart churn" to a clean -32000 error at 8 MiB.

## Documented follow-ups (NOT changed here — one item/iteration)
- `_read_json_response` (lines ~257-304) is DEAD CODE (no caller) and carries the same aread-then-check
  + `response.text` reads — remove or fix in a future pass.
- The error-body reads (`response.text[:500]` / `(await response.aread())[:500]`) read the whole
  untrusted error body before slicing — a smaller mem edge, cap for consistency later.
- `_validate_upstream` does allowlist (allowed_hosts) matching but does NOT resolve the hostname to
  reject an allowlisted host that RESOLVES to an internal/metadata IP (the sandbox-side analogue of
  CHG-0065's gateway SSRF guard) — relevant while the per-org sandbox network is internal=false
  (open NAT); a candidate follow-up (or close via network egress-lockdown, the infra task in item 12).
