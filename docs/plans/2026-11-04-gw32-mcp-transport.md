# GW32 / GW17b / GW17c — implementation record

Source of truth for the design: `.kiro/specs/gw32-mcp-transport/` (requirements, design, tasks).
Branch: `gw32-mcp-transport`. Status: code on this tree, offline gates green, live gate not run.

## Build order that was followed
Foundation and Requirement 1, then GW17b, then GW17c, then GW32 transports and OAuth. (GW17c was
built before GW32 because it needs only the foundation and `_forward`; both use the same
`_forward`, so no transport can bypass the allow-list, the cap or the credential gate.)

## What each layer holds
| Layer | New or changed |
|---|---|
| runtime | `resources_mcp.py` (every new capacity value), `store_keys_mcp.py` (`{rv2}` keys) |
| domain | `Principal.mcp_allowed_tools/mcp_max_tool_calls`, `DENY_ALL_TOOLS`, `mcp_credential.py`, `mcp_path.py`, posture codes |
| admit | `identity.principal_of` strict parse, `mcp_policy.py`, `mcp_cap.py`, `mcp_metrics.py`, `mcp_sessions.py`, `oauth_store.py`, `oauth_server.py` |
| dispatch | `mcp_credentials.py`, `mcp_transports.py`, `mcp_broker.py` (upstream headers argument), `mcp_refresh_lock.py`, `mcp_upstream_oauth.py`, `pinned_transport.py`, `url_guard.resolve_checked` |
| edge | `mcp_identity.py`, `mcp_paths.py`, `mcp_session_gate.py`, `mcp_http.py`, `wire/mcp_sse.py`, `mcp_cred_gate.py`, `mcp_policy_gate.py`, `mcp_ws.py`, `mcp_wiring.py`, `oauth_routes.py`, `mcp.py`, `mcp_registry.py`, `surface_entries.py`, `mcp_boot.py`, `mcp_upstream_routes.py`, `publisher.py` (`attach_mcp_policy`), `serve.py` (WebSocket wrap) |
| state_control | `source*.py`, `projector.py` (policy projection, off by default), `service.py` (flag) |
| control | credential read and refresh-result endpoints, `credential_version`, scan-version bump on credential writes; `gateway_oauth_flow.py` (status, flow, consume, oauth-tokens) + migration 0019 |
| lint | `check_capacity_literals.py` allowlist, new `check_credential_flow.py` |

## Superseded tests, updated and not deleted
- `tests/edge/test_mcp_route.py`: a bearer server now returns `mcp_upstream_credential_unavailable`
  (503) when no credential service is wired, instead of `mcp_upstream_auth_unsupported`. New rows for
  `query_param` (still unsupported), `needs_reauth` and an unknown transport.
- `tests/edge/test_mcp_registry.py`: `.needs_auth` replaced by `.auth_type`.
- No GW17 test asserted that `initialize` answers without a key, and none asserted a websocket scope
  is declined for an MCP path; nothing else needed changing.

## Deliberate deviations recorded
- SSE framing is a new MCP module (the shipped encoder/decoder is chat-delta shaped): design "SSE framing".
- `McpStoreKeys` lives in `store_keys_mcp.py` (R17.8 names `store_keys.py`): design amendment A-4.
- The allow-list denial refunds the budget through the existing hold release (a refusal before
  `mark_dispatched()` already refunds), not through a second `refund` call.
- Cap window is 60 s (D-6, v1 parity). Authoritative source: requirements D-6 ("v1's meaning ...
  60 second window ... a contract-derived bound, not a literal at the call site"). The value is
  `CAP_WINDOW_S` in `runtime/resources_mcp.py`, reached only through `derive_cap_bounds(contract)`;
  `admit/mcp_cap.py` holds no literal (capacity gate).
- Upstream metadata redirects are refused outright, stricter than the design's re-pinned hop budget.

## Not built
Offline real-socket upstream fixtures, pre-change route-table goldens, production-profile MCP wiring.
See the GW32 section of `docs/AI_MESH_RUNBOOK_v3.1_PROGRESS.md`.
