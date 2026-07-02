# P4.13/P6.18 §4 — gateway egress network assertion (stdio fleet: PASS)

`scripts/mcp_egress_assert.py` reads the gateway container's ESTABLISHED outbound
connections from `/proc/net/tcp[6]` (via docker exec — no extra tooling) BEFORE and
DURING a live 15-MCP tool-call burst, and asserts the burst opens NO new EXTERNAL
connection (only internal broker/control) — i.e. the gateway never dials an MCP
upstream directly.

## Result (all-stdio fleet — the transports actually in use): **PASS**
- Baseline external (infra, untouched by tool calls): 4× `:443` to AWS
  (CloudWatch/S3, `ec2-13-207-123-44.ap-south-1…`) + Cloudflare.
- During a 90-call burst (15 MCPs × 6): **+17 new connections, ALL INTERNAL** —
  broker `172.18.0.9:8311` + control `172.18.0.6:8000` + client sockets; **0 new
  external**. `broker(:8311) observed: True`.
- Conclusion: stdio MCP egress goes gateway → broker → sandbox ONLY. No direct
  gateway→upstream dial. The per-org egress isolation holds for stdio + mcp-remote
  (Linear), which is the entire live fleet.

## This harness is ALSO the acceptance gate for §3 (HTTP routing)
When a streamable-http/sse server is exercised, IF the gateway still dials the
upstream directly (§3 proxy refactor not landed), this harness reports a `SUSPECT
external dial` and FAILS — so it is the objective gate for the §3 `mcp_proxy` change
(route HTTP/SSE via `broker_send_rpc`). Today the fleet is all stdio, so §4 passes;
the HTTP leg of §4 is unblocked once §3-proxy lands.

## P4.13/P6.18 overall status
- §1 broker unified route: DONE + deployed (aa30d807)
- §2 gateway `broker_send_rpc`: DONE + tested (1aba6304)
- §3 sandbox image (unified agent, HTTP dial + egress allowlist): DONE + deployed (3c54d339)
- §4 egress assertion (stdio): **DONE + PASS**
- §3 proxy refactor (route HTTP/SSE via broker_send_rpc in gateway + control tools/call):
  REMAINING — cross-service; the only HTTP-transport servers are test rows (no
  production streamable-http). Parallel loop (cursor iter27) is on the proxy leg.
