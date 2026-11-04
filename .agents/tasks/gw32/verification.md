# GW32 / GW17b / GW17c — verification (iteration 2: review fixes)

Worktree `/work/suraj/AI_Mesh_Firewall/.worktrees/gw32`, branch `gw32-mcp-transport`. Iteration-2 commits
`e98eaf05..HEAD` on top of iteration 1 (`897b2f82..83cd0c5d`). Nothing pushed. The live Docker / staging gate
(DL-1..DL-7) was NOT run and is NOT claimed. Gateway venv `/tmp/gw32venv` (Python 3.12.3); control venv
`/tmp/gw32ctl`. The only containers used were two throwaway test databases for the control tests
(`pgvector/pgvector:pg16` on 127.0.0.1:55432 and `redis:7.4-alpine` on 127.0.0.1:56379), not the compose stack.

## Findings and what was done

| # | Finding | Resolution | Evidence |
|---|---|---|---|
| 1 | No production wiring | `edge/mcp_boot.extend_mcp_deps` is the one construction site; `build_dev_mcp` calls it (dev profile is the only boot path that builds MCP at all; the production profile builds no MCP, pre-existing since GW17). It builds `ValkeySessionStore`, `CallCapTable`, `CredentialService` (+ `ValkeyRefreshLock`/`LocalRefreshLock`), `OAuthServer`+`OAuthRoutes`+`OAuthTokenResolver`, `McpPolicyMetrics`, `UpstreamOAuthRoutes`, origins, public base. `dev_profile` passes the identity resolver and attaches the metrics. `serve._assemble_app` wraps the WebSocket scope with one shared `WsLimiter`. `DevStaticIdentity.resolve_hash` added so tokens/OAuth work in dev. Fail-closed: no store → no sessions, capped keys refused, OAuth 503; partial OAuth config refuses to boot; control URL neither https nor loopback → credentials and setup routes unset. | `tests/edge/test_gw32_boot_wiring.py` (12): every service built, OAuth discovery live (200), unset → 503, partial → refuses boot, session issued on initialize, allow-list enforced by boot-built services, capped key admitted by boot-built cap table, capped key refused without store, publisher series, cache drop scope, `serve._assemble_app` accepts an MCP WebSocket. |
| 2 | C4 upstream OAuth + production TokenEndpoint + pinned dial missing | Built. `dispatch/url_guard.resolve_checked` (same checks as `check_remote`, returns approved IPs; `check_remote` now delegates to it, behaviour unchanged). `dispatch/pinned_transport.py`: `PinnedTransport` dials one approved IP with original `Host` and SNI, no second resolution; `pinned_http` (no redirects, bounded read); `pinned_token_endpoint` is the production `TokenEndpoint`. `dispatch/mcp_upstream_oauth.py`: RFC 9728 → RFC 8414 discovery with issuer equality, https-only endpoints, S256 required, DCR, PKCE, exchange, `ControlOAuthFlowClient`. `edge/mcp_upstream_routes.py`: `oauth/start` (Origin, identity, org match, refuses `zsmcp_` token and restricted keys with audit `oauth_start_refused_restricted`, `force=true` when authorised, registered URL only incl. stdio `mcp-remote` arg, 503 without public URL), `/gateway/oauth/callback` (single-use state, fixed pages, nothing echoed), `oauth/status` (exactly 5 fields). Unwired → same routing-miss bytes as before. Control: `mcp_connector/gateway_oauth_flow.py` (status / flow / consume / oauth-tokens) + migration 0019. Ledger row `oauth/start` → `build`, owner GW32. | `tests/dispatch/test_gw32_pinned_transport.py` (5: approved IP + Host + SNI with one resolution, rebinding second answer gets zero bytes, mixed answers refused with zero bytes, 3xx returned not followed, oversize raises). `tests/edge/test_gw32_upstream_oauth.py` (20). Control `test_gateway_oauth_flow.py` (8). Ledger `compare()` → `[]`. |
| 3 | Policy metrics not published | `Publisher.attach_mcp_policy` adds `McpPolicyMetrics.SERIES_COUNT + 1` series incl. `amf_mcp_policy_enforcement_version` (`admit/mcp_metrics.ENFORCEMENT_VERSION = 1`); SERIES_COUNT assertion holds. Dev profile attaches it. | boot-wiring test `test_publisher_exports_policy_series_and_enforcement_version`. |
| 4 | Credential cache not invalidated on version poll | `McpRegistry.subscribe_drop(on_drop, extra_keys)`: the poll also watches every `(org, server)` the credential cache holds and calls `CredentialCache.drop_scope` on a version change. | boot-wiring `test_credential_cache_is_dropped_when_the_registry_version_changes`; existing registry poll tests pass. |
| 5 | Control credential endpoint tests never run | Run on throwaway Postgres + Redis. Found and FIXED a real defect: write-back for a missing or foreign server returned 409 instead of the uniform 404. Two test bugs fixed (unauthenticated is DRF 401 from the shared `IsGatewayInternalOnly`; the serializer sweep now renders only `MCPServerRegistration` model serializers and asserts it rendered). | see command table. |
| 6 | P5 sinks; P3/P6/P8/P10 < 100 | New `tests/edge/test_gw32_p5_sinks.py`: 120 iterations through the real route over bearer/header/basic × list/call × reflected or not; asserts the secret (and its 8-char prefix) is absent from every audit record (`serialize()`, repr, `as_mapping`, incl. the console block), the `Publisher` exposition, all log records, the response body+headers and broker transport headers, and present in the broker upstream body. Two negative controls (secret injected into audit `detail`; secret logged) turn the property red. P3, P4, P6, P8 now run 200 outer iterations, P10 200. | targeted runs below. |
| 7 | WS SSE-framed reply; in-flight revocation; D-8 | `_run_message` strips `Accept` from the synthetic POST scope, so a frame reply is always one JSON document (negative control: reverting it fails the new test). A watchdog re-checks identity every `stream_recheck_s` while a message is in flight; on revocation it cancels the call and closes 1008. `WsLimiter(slot_budget=connection_budget())` refuses a socket when `ceil(open × ws_idle_weight)` would exceed the budget; each message already takes a full admission slot and budget through the shared pipeline. | `tests/edge/test_gw32_ws.py` (12, 3 new). |
| 8 | Cap window 60 s vs "contract-derived" | No code change. Authoritative: requirements D-6 ("v1's meaning ... 60 second window ... a contract-derived bound, not a literal at the call site"). `CAP_WINDOW_S` lives in `runtime/resources_mcp.py`, reached only via `derive_cap_bounds(contract)`; `admit/mcp_cap.py` has no literal (capacity gate exit 0). Recorded in `docs/plans/2026-11-04-gw32-mcp-transport.md`. | — |

No finding needed a user-visible behaviour change, so no warning was raised. Changes that alter behaviour only
where it was unwired or wrong: setup paths answer when wired (unwired keeps today's routing-miss bytes); the
control write-back for a missing/foreign server is 404 (was 409; the gateway treats any non-200 the same).

## Commands run and results

Gateway, cwd `gateway_v2/`:

| Command | Result |
|---|---|
| `pytest -q -p no:cacheprovider tests` (full offline), on HEAD `3f71c99b` | **1 failed, 3248 passed, 106 skipped** (598 s). The one failure is `tests/gates/test_check_tenant_scale.py::test_the_shipped_data_plane_is_clean`, which reports only the pre-existing `pinecone_transport.py:140` (same as iteration 1). Output: `docs/plans/evidence/2026-11-04-gw32/pytest-full-iter2.txt`. |
| first full run, on the tree before the `drop_scope` rename | 1 failed, 3247 passed, 106 skipped: `test_check_tenant_scale` (pinecone pre-existing + `mcp_boot.py:222` `cache.keys()`, then fixed) |
| `pytest tests/edge/test_gw32_boot_wiring.py` | 12 passed |
| `pytest tests/edge/test_gw32_upstream_oauth.py` | 20 passed |
| `pytest tests/dispatch/test_gw32_pinned_transport.py` + url_guard tests | 33 passed |
| `pytest tests/edge/test_gw32_p5_sinks.py` | 3 passed |
| `pytest tests/edge/test_gw32_ws.py` | 12 passed; Accept negative control: with the fix reverted the new test fails |
| `pytest tests/admit/test_gw32_policy_cap.py tests/dispatch/test_gw32_credentials.py tests/admit/test_gw32_sessions.py` (iterations raised) | 25 passed |
| `mypy --strict gateway_v2 state_control audit_control` | 9 errors, all `import-not-found` for optional vector-store client libraries not installed in this venv (unchanged from iteration 1); 0 other errors, 275 files |
| `ruff check gateway_v2 state_control audit_control lint tests` | All checks passed |
| `lint-imports` | 3 contracts kept, 0 broken |
| `python -m lint.check_capacity_literals gateway_v2` | exit 0 |
| `python -m lint.check_no_module_mutable gateway_v2` | exit 0 |
| `python -m lint.check_frozen_dataclasses gateway_v2` | exit 0 |
| `python -m lint.check_http_outside_edge_resolve gateway_v2` | exit 0 |
| `python -m lint.check_credential_flow gateway_v2` (covers `mcp_upstream_*`) | exit 0 |
| `python -m lint.check_sizes gateway_v2` | exit 0 (`build_dev_dependencies` kept ≤ 120 lines via `_attach_mcp`) |
| `python -m lint.check_console_inventory`, `python -m lint.check_dispatch_provenance` | exit 0 |
| `python -m lint.check_tenant_scale gateway_v2 state_control` | exit 1, ONE violation: `dispatch/vector_transports/pinecone_transport.py:140` (pre-existing, untouched) |
| `python -c "from lint.admin_ledger import compare; print(compare(Path('..')))"` | `[]` |

Control, cwd `control/`, `DEBUG=true REDIS_URL=redis://127.0.0.1:56379/0 DATABASE_URL=postgresql://postgres:t@127.0.0.1:55432/t`:

| Command | Result |
|---|---|
| `python manage.py test mcp_connector --noinput` | Ran 85: 84 OK + 2 expected failures, 1 FAIL `test_oauth_transport_guard.test_websocket_ws_scheme_url_is_allowed` (needs DNS for `ws-everything.stub`; pre-existing, file untouched by this branch) |
| includes `test_gateway_credential_endpoints` (permission, org scoping, 404 uniformity, CAS, scan-version bump, console serializers carry no secret) | 10 passed after the 404 fix |
| includes `test_gateway_oauth_flow` (permission, store/consume once, expiry clears, malformed 404, foreign org uniform 404, reused client keeps secret, status has no secret, token write bumps versions, org-scoped write) | 8 passed |
| `python manage.py makemigrations mcp_connector --check --dry-run` | No changes detected |

## Not done / open

- Offline real-socket upstream fixtures (HTTP+SSE / WebSocket servers) and pre-change route-table goldens
  (iteration-1 gaps, not in this review's findings).
- Production-profile MCP wiring (the production profile builds no MCP deps at all; GW36).
- Upstream metadata 3xx is refused outright (stricter than the design's re-pinned hop budget).
- Live gate DL-1..DL-7: not run.
