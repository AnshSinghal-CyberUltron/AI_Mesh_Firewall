# P4.13/P6.18 — Cursor-owned agent bug blocks functional HTTP-via-sandbox

**Discovered validating §3-proxy end-to-end** with a real `streamableHttp` MCP
server (`@modelcontextprotocol/server-everything streamableHttp`, on the sandbox
bridge at 172.19.0.3:3001, `/mcp` endpoint, SSE responses).

## Symptom
Every remote-transport round-trip through the sandbox HANGS to `method_timeout`:
- broker `/rpc` (streamable-http, tools/list) → empty / timeout
- direct agent `/rpc` initialize → **timed out (25s)**
- direct agent `/rpc` tools/list → **timed out (60s)**

A plain urllib POST from the broker to `http://172.19.0.3:3001/mcp` returns the
`initialize` SSE frame **immediately** (`event: message\ndata: {...}`), so the
upstream server is healthy — the hang is in the AGENT.

## Root cause (`services/mcp-broker/sandbox-image/agent/upstream_manager.py`)
`_post_streamable_http` (~line 357) uses a **non-streaming** `session.client.post(...)`:

```python
response = await session.client.post(session.url, json=message, headers=headers, timeout=method_timeout)
```

The streamable-http server answers with `Content-Type: text/event-stream` and keeps
the SSE connection OPEN (for server→client notifications). A non-streaming `.post()`
waits for the FULL body / EOF, which never comes → blocks until `method_timeout`.

## Fix (Cursor — agent is Cursor-owned per PARALLEL_CLAIMS)
Stream the POST and return on the first JSON-RPC `data:` frame whose `id` matches
the request — mirror the SSE parsing already in `_ensure_sse_endpoint` (line 313):

```python
async with session.client.stream("POST", session.url, json=message, headers=headers, timeout=method_timeout) as response:
    # capture Mcp-Session-Id header if present
    data_lines = []
    async for line in response.aiter_lines():
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())
        elif line == "" and data_lines:
            msg = json.loads("\n".join(data_lines))
            if msg.get("id") == message["id"]:
                return _wrap_response(msg, ...)   # first matching response ends the read
            data_lines = []
```
Also auto-`initialize` the session on the first non-initialize method (the sandbox
receives single methods; the direct gateway path did initialize+list itself).

## Impact on the 4-transport goal
- Claude-owned wiring (§1 broker route, §2 broker_send_rpc, §3-proxy `_adapter_forward`
  route, §4 egress harness): **DONE + tested + committed + deployed**. `_adapter_forward`
  correctly calls `broker_send_rpc` for streamable-http/sse (unit-proven).
- stdio + mcp-remote (the ENTIRE live fleet): **fully functional + isolated** (P9 gate
  3× + egress-assert PASS).
- streamable-http/sse THROUGH the sandbox: **blocked on this agent bug** (hangs). The
  `MCP_HTTP_VIA_SANDBOX` flag is therefore left **default OFF** (legacy direct path)
  until the agent streaming-read is fixed; flipping it on today would hang HTTP tool
  calls. Once fixed: flip default on + re-run `mcp_egress_assert.py` against an HTTP
  server (must show 0 gateway→upstream dials) + `mcp_sandbox_transport_verify.py`
  ROUNDS=3 with all 4 transports.
