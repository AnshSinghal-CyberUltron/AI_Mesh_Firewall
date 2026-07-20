# Transport stubs + Linear OAuth + compliance mapping — live verify (2026-07-07)

## TODO 3 — Transport stubs

- `scripts/ensure_org_sandbox_network.sh` → `mcp_sandbox_net_zeroshield` exists
- `docker compose --profile transport-stubs up -d` → http/sse/ws-everything containers healthy
- DNS from sandbox network:
  - `http-everything.stub` → 172.20.0.6
  - `sse-everything.stub` → 172.20.0.4
  - `ws-everything.stub` → 172.20.0.5
- `mcp_sandbox_transport_verify.py` ROUNDS=1: **stdio PASS** (13 tools, echo ok); **http/sse/ws FAIL** tools=0 (pre-existing — gateway container may need rebuild/redeploy for latest sandbox routing; stubs are reachable via DNS)

## TODO 7 — Linear OAuth

- Control API `linear-manual-oauth`: `oauth_authorized=True`, `needs_reauth=False`
- POST `/api/mcp-connector/servers/{id}/tools/` → **synced 47 tools**, `connection_status=connected`

## TODO 9 — Compliance

- Empty `compliance_frameworks` in firewall config does not block MCP event `compliance_tags` (tags still present on audit events when scans fire).

## TODO 10 — 16-MCP matrix (gateway tools/list)

- 16 registered servers; **12/16** returned `tools>0` via `POST /gateway/zeroshield/mcp/{slug}` (simulator key)
- Failures (4):
  - `http-everything-stub`, `sse-everything-stub`, `ws-everything.stub` → SSRF blocks resolved stub IPs as internal (need `MCP_ALLOW_INTERNAL_HOSTS` + sandbox routing on running gateway)
  - `linear-manual-oauth` → `upstream returned 401` on **org gateway path** (token mirror endpoint not yet in running gateway container; control internal sync path still works with forwarded bearer — 47 tools)

**Deploy note:** Rebuild/restart `gateway` + `control` containers to pick up `oauth-token-mirror`, callback sync, and stub error messaging.

## Follow-up (2026-07-07 post-deploy)

- Rebuilt/recreated `gateway`, `control`, `mcp-broker`, `mcp-sandbox-image`; recreated `zeroshield-mcp-sandbox`.
- **Bugfix:** stub SSRF exemption used wrong suffix (`.everything.stub` vs actual `*-everything.stub` hostnames); fixed in `upstream_manager.py`.
- **Linear gateway path:** `tools/list` → **47 tools** after `_mirror_oauth_token_to_gateway` backfill.
- **Stubs (gateway tools/list):** `ws-everything-stub` OK (1 tool); `linear-manual-oauth` OK (47); `http-everything-stub` returns streamable-http session error (400 — MCP session handshake, not DNS/SSRF); `sse-everything-stub` can time out on slow cold-start.
