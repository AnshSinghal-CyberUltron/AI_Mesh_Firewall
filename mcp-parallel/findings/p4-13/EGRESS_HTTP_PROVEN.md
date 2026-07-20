# P4.13/P6.18 §4 — HTTP-transport egress isolation PROVEN (adversarial)

The stdio §4 egress assertion is trivial (stdio has no upstream). The meaningful
test is the HTTP transport: with `MCP_HTTP_VIA_SANDBOX` on, an org's streamable-http
tool call must go gateway→broker→SANDBOX→upstream — the GATEWAY must never open a
socket to the HTTP MCP host.

## Method
Registered zeroshield `http-everything-stub` → `http://http-everything.stub:3001/mcp`
(stub on `mcp_sandbox_net_zeroshield`, resolves to 172.20.0.4). Fired live streamable-
http `tools/call` echoes through the gateway; decoded `/proc/net/tcp` remote endpoints
of BOTH the gateway container and the zeroshield sandbox for `172.20.0.4:3001`.

## Result — PASS
```
echo via gateway: "Echo: egress-confirm"   (works end-to-end)
GATEWAY → 172.20.0.4:3001 : NONE            (isolation holds — no direct upstream dial)
SANDBOX → 172.20.0.4:3001 : 8 connections   (the per-org sandbox dials the upstream)
```
- The gateway never connects to the HTTP MCP host in ANY TCP state.
- The per-org sandbox is the sole dialer (egress-allowlisted inside the sandbox).

Note: sample TCP state — sandbox→upstream connections are often in TIME_WAIT (`06`)
between calls, not ESTABLISHED (`01`); an egress sampler must count all states (or,
as here, assert the GATEWAY has ZERO in any state — the robust invariant).

## Conclusion
All four transports route through the per-org sandbox; egress isolation is now proven
for BOTH stdio (fleet + gate) and streamable-http (this test). The gateway is never
the dialer for any MCP upstream.
