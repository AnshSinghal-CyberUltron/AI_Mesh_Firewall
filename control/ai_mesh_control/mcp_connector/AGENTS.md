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
- **B2 — pending-state FIXED (item #15, frontend):** `MCPConnectorPanel.renderServerCard` now renders a
  distinct amber **"Pending authorization"** connection badge (replacing the misleading grey "Unknown")
  and **"Authorize to load tools"** (replacing "0 tools") whenever `awaitingAuth` (=`serverAwaitingAuth`
  && !needs_reauth). Sync stays gated by `syncBlockedForAuth`. Backend note still applies: the OAuth
  callback (`views.py` → `_store_oauth_tokens`) flips `oauth_authorized` True but does **not** auto-sync
  — tools appear on the next (now-unblocked) sync; a fresh *unauthorized* oauth sync returns
  `([], "needs re-authentication")` setting `connection_status="failed"` + `needs_reauth=True`.
  (Optional future nicety: auto-resync right after a successful callback.)
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

## Client-facing error sanitization (CP16 — bug #5/6/7)
- **`last_sync_error` is the CLIENT boundary** (set in `_persist_sync_state`/discovery; rendered as the
  inline modal error on the MCP page). It must NEVER carry raw upstream HTML, exit codes, `proc.key`
  server keys, sandbox-agent internals, or "gateway logs" hints.
- **`_sanitize_sync_error(raw, *, org_slug, server_slug)` (views.py:407)** is the single choke point:
  logs the raw detail at WARNING under a 12-hex correlation `ref` (`uuid4().hex[:12]`), then returns a
  category-branded summary (auth / start-failure / egress / timeout / generic) + `(Ref: <ref>)`.
  ALL 4 return points in `_discover_tools_via_gateway` route through it — so any raw message the gateway
  bubbles up (incl. `mcp_stdio_adapter.py` "exited with code N" and raw upstream bodies) is scrubbed
  before it reaches the client. Add new discovery-error returns via this helper, never raw.
- `mcp_proxy.py:1838` is a PII-block **event recorder** (`decision="block"`), not a client error leak.
- VERIFY: `python3 scripts/ralph/mcp_page_cp16_clean_error.py` (direct control API; http_405 + stdio
  bad-command → clean branded + ref, `leaks=[]`). Client error CODE + dev debug view = CP17/CP18.

## Stable client-facing MCP error codes (CP17)
- `_classify_sync_error(low)` (views.py) maps a raw error → `(code, branded summary)`. The **codes are
  a public contract** (MCP_AUTH_FAILED / MCP_EGRESS_DENIED / MCP_OUT_OF_MEMORY / MCP_SERVER_CRASHED /
  MCP_IMAGE_UNAVAILABLE / MCP_TIMEOUT / MCP_START_FAILED / MCP_UNAVAILABLE) — the message wording may
  change, the code must not. **Order matters:** OOM (exit -9 / 137), crash (exit -6 / 134 / sigabrt),
  and image-missing are matched BEFORE the generic "exited with code" branch (those raw strings also
  contain "exited with code").
- `SyncError(str)` (views.py) is a `str` subclass carrying `.code` + `.ref`. It is returned by
  `_sanitize_sync_error`, so `last_sync_error = sync_error` stores the message, `.lower()` heuristics
  scan the message, `json`/DRF serialize it as the message — while `_resync_server_tools` and the sync
  view read `.code`/`.ref` to emit `error_code` + `correlation_id`. Do NOT downcast it to `str()` before
  the response reads its attributes.
- The OAuth refresh-fail path (`_ensure_oauth_token_fresh`) must route its `{exc}` through
  `_sanitize_sync_error` — never interpolate the raw exception into `last_sync_error`.

## Developer diagnostic channel (CP18)
- The REAL cause behind a sanitized client error is written by `_store_sync_diagnostic(ref, code, raw,
  ...)` to the Django cache (`django_redis`; locmem in tests) at key `mcp:diag:<ref>` with a 7-day TTL.
  It is best-effort — a cache outage must NOT break the sync path (the client already has message+code+ref).
- `MCPDiagnosticDetailView` (`GET /api/mcp-connector/diagnostics/<ref>/`, `permission_classes=[IsAdminUser]`)
  returns that record for **staff/superuser only**. An org client — even an org admin — is never Django
  `is_staff`, so the raw cause is never client-exposed. `ref` is validated `^[0-9a-f]{6,32}$`
  (invalid→400, expired/unknown→404).
- To debug a client-reported failure you only need the `(Ref: …)` they saw → GET the diagnostics endpoint
  as staff. VERIFY: `python3 scripts/ralph/mcp_page_cp18_diag_channel.py` (staff 200 sees raw cause;
  non-staff 403; edges 400/404). The script self-mints a non-staff JWT via `docker exec` — do NOT commit
  any token file.

## Enforcement-block accounting gotcha (CP22 diagnosis → CP23 fix)
- `MCPToolCallView` returns HTTP **403** for enforcement denials (`tool_disabled` :1199,
  `tool_not_registered` :1222, `schema_validation_failed` :1244) and **404** for unknown
  server/tool. It records these as `decision="block"` LOCALLY — but for **gateway-originated**
  calls the local `_record_event` is a **no-op** (de-dup shadow, ~:1166; "recorded once by the
  gateway data plane"). So the authoritative event comes from the gateway.
- BUG (CP22): the gateway (`mcp_proxy.py:3047`) records a non-200 backend response as
  `decision="error"` reason=`backend_error_http_{status}` — so a **403 enforcement block is
  miscounted as an error**, not a block. This is why the stats show few BLOCKs and many
  "errors" (`backend_error_http_403` dominates, = `tool_not_registered` denials of tools whose
  server crashed/never synced). Gateway↔control internal keys MATCH — it is NOT an auth failure.
- CP23 fix: at the gateway, map a control 403/404 carrying an enforcement `reason` in the body
  → `decision="block"` with that reason; keep genuine errors (backend 500, sandbox-unavailable,
  stdio crash) as `error`.
