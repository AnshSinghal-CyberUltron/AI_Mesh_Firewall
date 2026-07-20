# P3.8 — Sandbox transport contract draft

**Date:** 2026-07-02  
**Agent:** cursor-ralph-iter8  
**Story:** P3 scratchpad item #8  

## Summary

Drafted `docs/mcp/SANDBOX_TRANSPORT_CONTRACT.md` v1.0.0-draft — the bilateral seam between
gateway, broker, and sandbox-agent for all MCP transports.

## Contract highlights

- **Unified endpoint:** `POST /v1/sandbox/{org}/rpc` (broker) → `POST /rpc` (agent)
- **Envelope:** `SandboxRpcRequest` with `transport`, `stdio` block, `upstream` block
- **Transports:** stdio (existing), streamable-http, sse, websocket (P4.9–10)
- **Streaming v1:** buffered responses; v1.1 optional `/rpc/stream`
- **OAuth:** external control plane only; Bearer injection in `upstream.headers`
- **Egress:** `allowed_hosts` enforced by agent before connect
- **Errors:** `-32001` needs_reauth, `-32002` egress_denied, broker 502/503 mapping

## Hive-mind consensus

| Item | Value |
|------|-------|
| Proposal | `proposal-1782979656279-rgcvqe` |
| Cursor vote | YES (`cursor-ralph-iter8`) |
| Claude session | **PENDING** |
| Status | **PROVISIONALLY RATIFIED** |

Cursor may implement agent-side (P4.9+). Gateway/broker changes require Claude ACK.

## Artifacts

- `docs/mcp/SANDBOX_TRANSPORT_CONTRACT.md`
- `mcp-parallel/findings/p3-8/consensus.json`

## Next

**P4.9** — Implement HTTP/SSE upstream proxy in `sandbox-image/agent/` per contract §5.2–5.3.

## Blockers

- Full bilateral ratification blocked on Claude Code hive vote (non-blocking for Cursor agent work).
