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
| DL-4 | Real token server and real OAuth server (with real refresh) through control, broker and sandbox. Grep every response, container log and audit record for the secret. Check revocation within the registry TTL, and two real orgs on one stack. OAuth refresh needs the production token-endpoint transport, which does not exist yet. |
| DL-5 | Edit a key's allow-list and cap in the console. Check it reaches the gateway within the identity freshness bound (5 s), and that the cap holds across real workers on a real Valkey. |
| DL-6 | The GW26 MCP live claims that this card unblocks. |
| DL-7 | The C2 MCP replay (LGW17-7) and the fleet-scale LGW17-4, deferred as in GW17. |

Control tests: `control/ai_mesh_control/mcp_connector/tests/test_gateway_credential_endpoints.py` needs
Postgres (the SQLite test database cannot be created because of an existing migration). Run it with
`python manage.py test mcp_connector.tests.test_gateway_credential_endpoints` against a Postgres test
database. `test_gateway_credential_body.py` ran green offline.

## Wiring that is not done (do this before any live run)
- Nothing builds `McpDeps.sessions`, `oauth`, `token_store`, `credentials`, `cap_table`, `policy_metrics`
  or the `WsLimiter` outside tests. There is no `build_mcp_deps` and neither the dev nor the production
  profile serves the new surfaces. `wrap_mcp_app(app, ws_limiter)` exists for the WebSocket scope.
- No production token-endpoint transport (pinned dial) exists for OAuth upstream refresh.
- No upstream OAuth setup routes.
- `McpPolicyMetrics` is not published and `amf_mcp_policy_enforcement_version` does not exist.

## Deploy order for per-key policy (D-10)
1. Deploy the gateway with this code, with `AMF_PROJECT_MCP_KEY_POLICY` unset.
2. Check every replica runs it (there is no metric for this yet; check the image tag).
3. Set `AMF_PROJECT_MCP_KEY_POLICY=1` on the projector. Until then GW17c enforces nothing.
If it is set early, keys with an allow-list or cap are unrestricted on old replicas (declared residual).

## Open decisions (all have a conservative working default)
D-1 MCP client URL: kept `/gateway/{org}/mcp/{srv}`; no redirect; no migration notice; not owner-signed.
D-2 org in path (path org must equal the key's org, else 403). D-3 opaque `zsmcp_` token. D-4 control
keeps custody. D-5 capacity allowlist shape (shared with GW27: append `runtime/resources_org.py` to
`ALLOWED_RELS` when that lands). D-6 cap window 60 s. D-7 offline real-server proof split from live.
DD-3 gateway-side refresh. DD-8 cold-start bootstrap 1 (set 0 for exactness at the price of a 429 on a
cold worker's first call). DD-13 Origin default (same authority or `AMF_MCP_ALLOWED_ORIGINS`).
DD-16 `query_param` auth unsupported. D-8 WebSocket idle weight plus per-message slot (the literal
R5.7 is not what is built; `ws_idle_weight` is in the bounds but the limiter counts connections only).
D-9 credential replacement rules (not built). D-10 deploy order above.

Settings read by this code: `AMF_GATEWAY_PUBLIC_URL`, `AMF_OAUTH_HASH_KEY`,
`AMF_OAUTH_HASH_KEY_PREVIOUS`, `AMF_TRUSTED_PROXY_HOPS`, `AMF_MCP_ALLOWED_ORIGINS`,
`AMF_MCP_ALLOW_PLAINTEXT_CREDENTIAL_HOSTS`, `AMF_PROJECT_MCP_KEY_POLICY`. Only
`AMF_PROJECT_MCP_KEY_POLICY` is read by shipped boot code (the projector). The rest are the names the
wiring should use; nothing reads them yet.
