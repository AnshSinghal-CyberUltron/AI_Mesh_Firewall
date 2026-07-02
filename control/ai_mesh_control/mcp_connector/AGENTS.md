# mcp_connector (control plane) — agent notes

The Django control-plane for MCP servers: registration, OAuth 2.1 client, tool sync, per-tool
policy. It is the **source of truth** the gateway reads (via `/internal/*` + shared Redis) and the
frontend renders. Map + evidence: `docs/mcp/control-plane-flow.md`.

## Layout
- `models.py` — `MCPServerRegistration` (transport/url/command/auth_type/oauth_*/`oauth_authorized` property:178) + `MCPToolRegistration`.
- `serializers.py` — registration/update validation. **`MCPServerCreateSerializer.validate` (:121) is the authoritative registration guard, incl. the B1 oauth+transport guard (:177).**
- `views.py` — register/list/patch/delete, OAuth start+callback, tool sync, gateway-internal endpoints.
- `oauth.py` — OAuth 2.1 client primitives (discover/DCR/PKCE/exchange/refresh), all SSRF-re-guarded.
- `signals.py` — post_save/delete → `bump_scan_version` (Redis INCR only; no provisioning/resync).
- `tasks.py` — `record_mcp_event_task` audit writer (no resync task).
- `_url_guard.py` — `is_safe_outbound_url` fail-closed SSRF guard.

## Conventions / invariants
- **Registration validation is the B1 boundary.** `serializers.py:177` rejects `auth_type=="oauth"`
  unless `transport ∈ (streamable-http, sse)`. Keep it (test: `tests/test_oauth_transport_guard.py`).
  Rationale: a stdio server (incl. Linear via `mcp-remote`) authorizes upstream *inside the gateway
  sandbox* — its `auth_type` stays `"none"`; the URL for OAuth lives in mcp-remote args, not the row's `url`.
- **`oauth_authorized` is computed** (`auth_type=="oauth" and bool(auth_token)`) — never a stored column.
  It flips True only when `_store_oauth_tokens` (`views.py:298`) saves a token.
- **Tools appear only after sync.** Register/patch/oauth-callback do NOT create tool rows. The only
  sync trigger is `POST /servers/{pk}/tools/` (`MCPServerToolListView.post:1889` → `_resync_server_tools:507`)
  and the `resync_mcp_servers` management command. Tool rows are API-read-only.
- Secrets (`auth_token`, `oauth_client_secret`, `oauth_refresh_token`, `oauth_code_verifier`, …) are
  Fernet-encrypted at rest (`enc:` prefix). Never log or serialize them raw.
- Server config edits are single-source-of-truth here; the gateway re-reads via a 120s Redis TTL
  cache, invalidated by the `bump_scan_version` signal. If you add a config field the gateway needs
  promptly, add it to `_SERVER_RELEVANT_FIELDS` (`signals.py:167`) so a save bumps the version.

## Gotchas / known issues (targets for B1/B2/B3 fixes)
- **B1 bypass — FIXED (item #13):** `MCPServerOAuthStartView.post` now calls
  `oauth_http_transport_error(server)` FIRST (module fn near the view) which enforces
  `transport ∈ (streamable-http, sse)` *before* the url check and *before* any `auth_type` write.
  A stdio/websocket row gets a clear transport error (not the misleading "Server has no URL"), can
  never reach discovery, and can never be flipped to `auth_type="oauth"` (the old bypass at the
  `server.save(... "auth_type" ...)` block is now reachable only for validated HTTP rows). "Server has
  no URL" is now reachable ONLY for a corrupted HTTP-transport row with an empty url (unreachable via
  registration — the serializer requires url for HTTP). The stdio+mcp-remote (Linear) case still
  authorizes via the **gateway** `oauth/start` path (that button is legit, NOT a duplicate to delete).
  Test: `tests/test_oauth_transport_guard.py::OAuthStartViewTransportGuardTests` (SimpleTestCase, no DB).
- **B2:** the OAuth callback (`views.py:2658` → `_store_oauth_tokens`) flips `oauth_authorized` True but
  does **not** trigger a sync — a fresh authorized server still shows 0 tools until a manual sync. A
  fresh *unauthorized* oauth server's sync returns `([], "needs re-authentication")` (`:448/452`),
  setting `connection_status="failed"` + `needs_reauth=True`. Frontend must render a distinct
  "Pending authorization" state (item #15); consider auto-resync after callback.
- **B3:** nothing here provisions the per-org broker sandbox — the gateway does it lazily on first
  discover/tool-call. Item #19 adds eager provisioning on register/authorize/first-sync.

## Tests
`cd control && python manage.py test mcp_connector` (or pytest). Key: `tests/test_oauth_transport_guard.py`,
`tests/test_scan_controls.py`, `tests/test_scan_version_bump.py`.

**Test-env gotcha (verified iter):** control migrations are **Postgres-native** (a `RunSQL`
`CREATE EXTENSION …`), so `manage.py test` on sqlite dies during `setup_databases` with
`near "EXTENSION": syntax error`. To run DB-independent tests without a Postgres stack, make them
`SimpleTestCase` (no DB) and run via pytest-django:
`PYTHONPATH=ai_mesh_control:../shared DJANGO_SETTINGS_MODULE=main_app.settings DJANGO_SECRET_KEY=x DEBUG=True DJANGO_CACHE_BACKEND=locmem pytest ai_mesh_control/mcp_connector/tests/test_oauth_transport_guard.py -k OAuthStartViewTransportGuard`.
Settings require `DJANGO_SECRET_KEY` when DEBUG=False; `main_app/__init__.py` imports `celery`; settings
imports `ai_mesh_shared` (repo `shared/` on path). DB falls back to sqlite only when `DATABASE_URL` unset.
