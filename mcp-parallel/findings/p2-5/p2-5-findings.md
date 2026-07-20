# P2.5 — OSS remote transport + mcp-remote research

**Date:** 2026-07-02  
**Agent:** cursor-ralph-iter5  
**Story:** P2 scratchpad item #5  

## Summary

Studied `modelcontextprotocol/servers`, MCP Streamable HTTP spec, and three major OSS
transport proxies (**mcp-remote**, **supergateway**, **mcp-proxy**). Full write-up:
`docs/mcp/oss-research-remote-transport-proxies.md`.

## Key answers

### How OSS proxies handle http/sse/ws

- **mcp-remote:** stdio client ↔ remote HTTP/SSE; OAuth 2.1 lazy + `--header` bypass; `http-first`/`sse-first` fallback strategies.
- **supergateway:** bidirectional — stdio↔SSE/WS/Streamable HTTP in both directions; no OAuth.
- **mcp-proxy:** Python stdio↔SSE/Streamable HTTP both ways; optional OAuth; container-friendly.

Official **modelcontextprotocol/servers** ships stdio by default; remote modes (`sse`, `streamableHttp`) are separate server processes — bridging to stdio-only clients always needs an external proxy.

### mcp-remote + Linear

Linear = remote HTTPS MCP requiring OAuth. Community pattern: register as
`npx mcp-remote https://mcp.linear.app/sse` (stdio row). ZeroShield gateway pre-injects
`--header Authorization: Bearer <token>` so sandbox stays headless. **Not** the same as
`auth_type=oauth` on a URL-less stdio row (B1 nuance — see `oss-research-oauth-transport-ux.md`).

### Implications for ALL transports in gVisor sandbox

| Transport | Today | Target |
|-----------|-------|--------|
| stdio | sandbox agent ✓ | keep |
| stdio+mcp-remote | wrapper in sandbox ✓ | keep until HTTP-native path proven |
| streamable-http/sse | **gateway dials upstream directly** | **in-sandbox httpx client**, egress allowlisted |
| websocket | **gateway ws adapter** | **in-sandbox ws client** |

OSS proxies validate the **pattern** (transport conversion + OAuth at the edge) but not our
multi-tenant isolation — that remains broker/gVisor + per-org volume. Native HTTP/WS clients
inside the sandbox agent replace both gateway-direct dialing and mcp-remote for HTTP rows.

## Artifacts

- `docs/mcp/oss-research-remote-transport-proxies.md` (primary deliverable)
- Cross-refs: `docs/mcp/oss-research-mcp-remote.md`, `oss-research-stdio-patterns.md`, `oss-research-oauth-transport-ux.md`
- `mcp-parallel/findings/p2-5/sources.json` (URL index)

## Next

P2.6 — gVisor + Docker hardening (`oss-research-docker-hardening.md` exists; extend in p2-6 findings).
