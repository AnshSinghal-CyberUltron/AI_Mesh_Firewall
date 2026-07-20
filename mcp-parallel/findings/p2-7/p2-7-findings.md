# P2.7 — MCP OAuth 2.1 + sandbox client research

**Date:** 2026-07-02  
**Agent:** cursor-ralph-iter7  
**Story:** P2 scratchpad item #7  

## Summary

Web research on MCP OAuth 2.1 (PKCE, RFC 9728/8414/8707), why OAuth requires HTTP URL,
stdio+mcp-remote vs `auth_type=oauth`, and in-sandbox HTTP/WS client implications.
Primary deliverable: `docs/mcp/oss-research-oauth-sandbox-client.md`.

## Key answers

### PKCE / RFC compliance (repo)

Repo OAuth **client** (gateway/control) implements PKCE S256, PRM (9728), AS metadata (8414),
DCR (7591), `resource` on authorize+token+refresh (8707), token passthrough forbidden.
STDIO oauth blocked at registration — spec-aligned. **Gap to verify:** refuse authorize if AS
metadata lacks `code_challenge_methods_supported` with S256.

### Why OAuth needs HTTP URL

Discovery (PRM, AS metadata), RFC 8707 `resource`, browser redirect, and token endpoint all
require HTTP(S). Pure stdio has no URL → `auth_type=oauth` on stdio is spec-invalid.

### stdio+mcp-remote vs auth_type=oauth

| | mcp-remote stdio | HTTP oauth row |
|--|------------------|----------------|
| auth_type | `none` | `oauth` |
| OAuth owner | gateway | gateway/control |
| Token to sandbox | `--header Bearer` | Bearer on httpx/ws (P4) |

### In-sandbox OAuth client

**Do NOT** run OAuth client (PKCE/browser/DCR) inside sandbox. Gateway/control owns control plane;
sandbox is data plane only (httpx/ws + injected Bearer). mcp-remote in sandbox is bridged via
gateway header injection. Contract must state `oauth_client_role: external_only`.

## Artifacts

- `docs/mcp/oss-research-oauth-sandbox-client.md` (new)
- Cross-refs: `oss-research-oauth-spec.md`, `oss-research-oauth-transport-ux.md`
- `mcp-parallel/findings/p2-7/sources.json`

## Next

**P3.8** — Draft `docs/mcp/SANDBOX_TRANSPORT_CONTRACT.md` (http/ws/sse proxy RPC shape, egress, OAuth injection).

## Blockers

None.
