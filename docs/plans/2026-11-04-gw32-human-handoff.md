# GW32 / GW17b / GW17c — human handoff

Nothing here was run. The offline suite is green; every item below needs the real stack. Do not mark
GW32, GW17b or GW17c done until the DL items you accept are run and recorded.

## Live gate (DL-1 .. DL-7)
Run from the repo root on the Docker stack, with the branch deployed.

| Item | What to run or check |
|---|---|
| DL-1 | Point VS Code or the MCP SDK client at `https://<gateway>/gateway/<org>/mcp/<srv>` with only the key changed. Run initialize, tools/list, tools/call. Expect all three to succeed. |
| DL-2 | With a real MCP client, run discovery, dynamic registration, authorize with PKCE and the token exchange against the running gateway (`AMF_GATEWAY_PUBLIC_URL` and `AMF_OAUTH_HASH_KEY` must be set, and the OAuth routes wired: see "Wiring" below), then call a tool. |
| DL-3 | Reach a real HTTP+SSE MCP server and a real WebSocket MCP server through the real broker and sandbox. Check `tools/list`, `tools/call`, the sandbox host allowlist and the SSRF check. The offline tests do NOT cover this. |
| DL-4 | Real token server and real OAuth server (with real refresh) through control, broker and sandbox. Grep every response, container log and audit record for the secret. Check revocation within the registry TTL, and two real orgs on one stack. OAuth refresh uses the pinned token-endpoint transport (`dispatch/pinned_transport.py`); check it against a real IdP. |
| DL-5 | Edit a key's allow-list and cap in the console. Check it reaches the gateway within the identity freshness bound (5 s), and that the cap holds across real workers on a real Valkey. |
| DL-6 | The GW26 MCP live claims that this card unblocks. |
| DL-7 | The C2 MCP replay (LGW17-7) and the fleet-scale LGW17-4, deferred as in GW17. |

Control tests: run on a throwaway Postgres (pgvector/pgvector:pg16) + Redis with
`DEBUG=true REDIS_URL=... DATABASE_URL=postgresql://... python manage.py test mcp_connector`
(SQLite cannot create the test database because of an existing Postgres-only migration). Iteration 2
ran them green except one pre-existing DNS-dependent test (see verification.md).
## Wiring (iteration 2)
- `edge/mcp_boot.extend_mcp_deps` is the one construction site. `build_dev_mcp` calls it, so the DEV
  PROFILE builds the session store, call-cap table, credential service, OAuth 2.1 server, token
  resolver, policy metrics and the upstream OAuth setup routes. `serve._assemble_app` wraps the
  WebSocket scope with one shared `WsLimiter` (idle-weight budget = `connection_budget()`).
- Without a store (no `AMF_DEV_REDIS_URL`): no session ids, capped keys refused, OAuth 503, refresh
  lock per-worker. A control URL that is neither https nor loopback leaves credentials and the setup
  routes unset (an upstream secret never crosses plaintext).
- The PRODUCTION PROFILE builds no MCP dependencies at all (pre-existing: GW17 is dev-profile only).
  Wiring MCP into the production boot belongs with GW36's production profile.
- `McpPolicyMetrics` is published through `Publisher.attach_mcp_policy` (7 series incl.
  `amf_mcp_policy_enforcement_version`).
## Deploy order for per-key policy (D-10)
1. Deploy the gateway with this code, with `AMF_PROJECT_MCP_KEY_POLICY` unset.
2. Check every replica reports `amf_mcp_policy_enforcement_version 1` on `/metrics`.
3. Set `AMF_PROJECT_MCP_KEY_POLICY=1` on the projector. Until then GW17c enforces nothing.
If it is set early, keys with an allow-list or cap are unrestricted on old replicas (declared residual).

## Open decisions (all have a conservative working default)
D-1 MCP client URL: kept `/gateway/{org}/mcp/{srv}`; no redirect; no migration notice; not owner-signed.
D-2 org in path (path org must equal the key's org, else 403). D-3 opaque `zsmcp_` token. D-4 control
keeps custody. D-5 capacity allowlist shape (shared with GW27: append `runtime/resources_org.py` to
`ALLOWED_RELS` when that lands). D-6 cap window 60 s. D-7 offline real-server proof split from live.
DD-3 gateway-side refresh. DD-8 cold-start bootstrap 1 (set 0 for exactness at the price of a 429 on a
cold worker's first call). DD-13 Origin default (same authority or `AMF_MCP_ALLOWED_ORIGINS`).
DD-16 `query_param` auth unsupported. D-8 WebSocket idle weight plus per-message slot: each message runs the HTTP pipeline (full
admission slot and budget charge); open sockets hold `ceil(n x ws_idle_weight)` slots against
`connection_budget()` (refused 1013 beyond it). D-9 credential replacement: `oauth/start` refuses a
`zsmcp_` token and a restricted key, needs `force=true` when already authorised; any unrestricted raw
key of the org can still replace it (residual, owner decision). D-10 deploy order above.

Settings read by this code: `AMF_GATEWAY_PUBLIC_URL`, `AMF_OAUTH_HASH_KEY`,
`AMF_OAUTH_HASH_KEY_PREVIOUS`, `AMF_TRUSTED_PROXY_HOPS`, `AMF_MCP_ALLOWED_ORIGINS`,
`AMF_MCP_ALLOW_PLAINTEXT_CREDENTIAL_HOSTS`, `AMF_PROJECT_MCP_KEY_POLICY`. All but
`AMF_TRUSTED_PROXY_HOPS` are read by the dev-profile boot (`edge/mcp_boot.py`) or the projector.
Upstream OAuth setup: deploy note, disable query-string access logging for `/gateway/oauth/callback`
(design residual). Upstream metadata redirects are refused outright (stricter than the design's
re-pinned hop budget).
