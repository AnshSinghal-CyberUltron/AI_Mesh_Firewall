# BACKSTOP hardening — sandbox agent bounded untrusted error-body read (CHG-0069)

- **Item:** G3 item 10 (resource limits ... mem ... + containment). Error-path counterpart of CHG-0066.
- **Change-id:** CHG-0069 (2026-07-02)
- **Type:** Mem-bomb containment (whole-body buffer on the error path) on the untrusted-upstream read.

## Gap
In the sandbox agent's streamable-http handler, an upstream error response (status >= 400) was read as:
```
body = (await response.aread())[:500].decode("utf-8", "replace")
```
`response` comes from `client.stream(...)`, so `aread()` buffers the ENTIRE (untrusted) error body into
memory BEFORE the `[:500]` slice. A malicious upstream returning a huge 4xx/5xx body (e.g. a 2GB error
page) OOMs the sandbox agent (up to the sandbox mem limit → kill + restart of that tenant's sandbox)
even though only the first 500 bytes are ever used. This is the ERROR-path counterpart of the CHG-0066
success-path fix (the JSON success branch was fixed then; the error snippet read was not).

## Fix — `upstream_manager.py`
New `async _aread_snippet(response, limit=1024)` streams `response.aiter_bytes()` and STOPS once `limit`
bytes are collected, returning at most `limit` bytes — never buffering the whole body. The error read
now uses it: `body = (await _aread_snippet(response))[:500].decode(...)`. Behaviour for a normal small
error body is unchanged (the message still carries the first 500 chars).

## Verification
- `cd services/mcp-broker && ./.venv/bin/python -m pytest sandbox-image/agent/tests/test_upstream_proxy.py -q -k "not websocket"`
  → 15 passed. New: (1) a 500 upstream response → `-32000 upstream HTTP 500: <bounded body>`; (2) unit —
  `_aread_snippet` over a 1000-chunk "huge" body with limit=250 returns <=250 bytes and CONSUMED <=3
  chunks (stopped early, did not buffer all 1000). The 3 `websocket` tests HANG in this env
  PRE-EXISTINGLY (unrelated — real ws / async-loop issue; the streamable-http tests all pass).

## Containment note
The sandbox mem limit (CHG-0015: 2GiB) contained the pre-fix OOM to the single tenant's sandbox
(auto-recovered). This reduces the blast radius from "sandbox OOM + restart" to a bounded read + clean
-32000 error.

## Documented follow-up (NOT changed — one item/iteration)
`_read_json_response` (lines ~257-304) remains DEAD CODE (no caller) and still contains the same
whole-body error reads (`response.text[:500]` / `(await response.aread())[:500]`); remove it in a
future cleanup pass.
