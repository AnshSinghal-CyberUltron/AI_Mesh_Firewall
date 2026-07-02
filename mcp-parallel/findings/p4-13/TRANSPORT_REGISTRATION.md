# P4.13 — four-transport manifest registration (post-§3)

**Owner:** Cursor (prep). **Blocked until** Claude lands `mcp_proxy` §3 routing.

## When to use

After `mcp_sandbox_transport_verify.py` wiring gate reports `wiring landed: YES`:

1. Register **one server per transport** in org `zeroshield` (or update slugs below).
2. Copy `TRANSPORT_MANIFEST.example.json` → a filled manifest with real `gateway_key` + slugs.
3. Run:

```bash
TRANSPORT_MANIFEST=mcp-parallel/findings/p4-13/TRANSPORT_MANIFEST.zeroshield.json \
  ROUNDS=3 python3 scripts/mcp_sandbox_transport_verify.py
```

4. Run tcpdump network assertion (printed by the script).
5. Run Playwright B1/B2/B4 if not covered by `mcp_p10_recursive_gate.py`.

## Manifest fields

| Field | Source |
|-------|--------|
| `org` | Org slug (`zeroshield`) |
| `gateway_key` | From control org settings or `scripts/ralph/.mcp_scale_manifest.json` |
| `servers.stdio` | Existing stdio slug, e.g. `everything-1` |
| `servers.streamable-http` | New HTTP MCP slug |
| `servers.sse` | New SSE MCP slug |
| `servers.websocket` | New WebSocket MCP slug |

## Per-transport registration (MCPConnectorPanel)

### stdio (ready today)

Already in scale manifest: `everything-1` … `everything-5` (npx `@modelcontextprotocol/server-everything`).

### streamable-http

| Field | Value |
|-------|-------|
| Name | `HTTP Everything stub` |
| Transport | `streamable-http` |
| Auth | `none` (or `oauth` for Linear — requires manual authorize) |
| URL | Public MCP HTTP endpoint or test stub |
| Slug | `http-everything-stub` (match manifest) |

**Smoke:** `tools/list` returns tools; `tools/call` echo with canary must round-trip via sandbox.

### sse

| Field | Value |
|-------|-------|
| Transport | `sse` |
| URL | `https://…/sse` (provider-specific) |
| Slug | `sse-everything-stub` |

### websocket

| Field | Value |
|-------|-------|
| Transport | `websocket` |
| URL | `wss://…/mcp` |
| Slug | `ws-everything-stub` |

## Stub options (dev)

If no public SSE/WS endpoints are available:

- Use an in-cluster MCP stub container on the docker network (broker egress allowlist must include its host).
- Document stub URL in a local-only manifest (do not commit secrets).
- stdio-only partial check: omit `TRANSPORT_MANIFEST`; script falls back to `SCALE_MANIFEST` (stdio only) with `WARN: partial manifest`.

## Verification checklist

- [ ] Wiring gate: `mcp_proxy` has `broker_send_rpc` refs; `mcp_proxy_direct_httpx` = false
- [ ] `ROUNDS=3` transport harness all-green
- [ ] tcpdump: zero gateway → external MCP :443 during harness
- [ ] Playwright B1/B2/B4 green
- [ ] Mark P4.13 / P6.18 `[x]` in scratchpad

## References

- `docs/mcp/gateway-integration-checklist.md` §5
- `docs/mcp/SANDBOX_TRANSPORT_CONTRACT.md`
- `scripts/mcp_sandbox_transport_verify.py`
