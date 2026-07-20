# Control-Plane MCP Connector — registration + oauth_authorized + tool-sync

> Deliverable for `scripts/ralph/mcp_progress.md` **P0 item #3**: map the Django control-plane
> `mcp_connector` register → oauth_authorized → tool-sync flow with `file:line` anchors.
> All anchors read + spot-verified (commit `fc268c58`). Dir: `control/ai_mesh_control/mcp_connector/`.
>
> This is the source of truth for the server registry the gateway reads and the frontend renders,
> so it decides **B1** (registration validation), **B2** (oauth_authorized + when tools appear),
> **B3** (does register/authorize provision the sandbox?). Feeds item #5.

## End-to-end flow

```
POST /api/mcp-connector/servers/  (MCPServerListCreateView.post, views.py:764)
  ├─ MCPServerCreateSerializer.validate (serializers.py:121)  ← ALL registration guards incl. B1
  ├─ get_or_create(org, name) (views.py:784)  → 409 on dup
  ├─ GatewayAPIKey.ensure_default_for_org (views.py:821)  ← the ONLY side-effect (a data-plane key)
  └─ 201 {registration, tools_count:0}   ← NO tool sync, NO sandbox provisioning
  post_save signal → bump_scan_version (signals.py:154) — Redis INCR only

POST /servers/{pk}/oauth/authorize/  (MCPServerOAuthStartView.post, views.py:2513)   [control OAuth path]
  ├─ require server.url else 400 "Server has no URL; OAuth is only for HTTP transports" (views.py:2524/2526)
  ├─ oauth.discover (oauth.py:169) + register_client DCR (oauth.py:237) + PKCE/state
  └─ server.auth_type = "oauth" (views.py:2582)  ← ⚠ BYPASSES serializers.py:177 transport guard
GET  /oauth/callback/  (MCPOAuthCallbackView.get, views.py:2658, AllowAny, matched by oauth_state:2672)
  └─ exchange_code (oauth.py:321) → _store_oauth_tokens (views.py:298/2697) → auth_token set
       → oauth_authorized flips TRUE (models.py:178)   ← does NOT trigger sync ("You can sync its tools")

POST /servers/{pk}/tools/  (MCPServerToolListView.post, views.py:1889/1906)   ← ONLY tool-sync trigger
  └─ _resync_server_tools (views.py:507) → _discover_tools_via_gateway (views.py:407)
       → gateway POST /v1/mcp/internal/discover-tools (views.py:465)
       → upsert MCPToolRegistration rows, prune-on-success, set tools_count/connection_status/needs_reauth
  (also invoked by `manage.py resync_mcp_servers`)
```

## 1. Model (`models.py`)

`MCPServerRegistration`: `url`, `transport` (choices), stdio `command`/`args`/`env_vars`, `auth_type`
(choices incl. `oauth`), `auth_token` (Fernet-encrypted `enc:`), `oauth_*` endpoint/client/token columns
(`:156-167`, encrypted for secrets), `needs_reauth` (`:176`), `connection_status`, `tools_count`
(default 0, read-only), `last_sync_at`/`last_sync_error`/`last_sync_attempt_at`.
- **`oauth_authorized` (property, `models.py:178`)** = `auth_type=="oauth" and bool(auth_token)`. Fresh
  oauth server → `auth_token=""` → **False**. Flips True only when `_store_oauth_tokens` saves a token.
- `gateway_endpoint` (property, `:201`) builds `/gateway/{org.slug}/mcp/{server_slug}` (+`GATEWAY_PUBLIC_URL`).
- Unique per-org: `(org, server_slug)` + `(org, name)` (`:185`); `MCPToolRegistration` unique `(server, tool_name)`.
- `save()` (`:196`) only slugifies `server_slug` — **no provisioning/broker/Redis side effects.**

## 2. Registration validation (`serializers.py`) — B1 lives here

`MCPServerCreateSerializer.validate` (`:121`, `_val` PATCH-fallback `:127`):
| Rule | line | effect |
|---|---|---|
| transport ∈ {streamable-http, sse, stdio, websocket} | `:132/134` | 400 unknown transport |
| stdio requires `command` | `:140` | 400 |
| non-stdio requires `url` | `:145` | 400 |
| url SSRF guard (`_url_guard.is_safe_outbound_url`, fail-closed) | `:158` | 400; ws allows ws/wss/http/https, else http/https |
| **B1: `auth_type=="oauth"` requires transport ∈ (`streamable-http`,`sse`)** | **`:177`** | 400 — blocks oauth+stdio AND oauth+websocket (hardcoded tuple, not `ALL_TRANSPORTS`) |
| bearer → `auth_token`; basic → user+pass; authheaders → ≥1 pair; query_param → key+value | `:190-242` | 400 (oauth intentionally NOT here → registers with blank `auth_token`) |

`local_model_data` (`:245`) filters to persistable columns; on PATCH emits only present keys so it
never wipes columns. The B1 guard is **correct** and matches the nuance (comment `:167-176`): stdio
servers (incl. Linear via `mcp-remote`) authorize upstream inside the gateway sandbox — their
`auth_type` stays `"none"`; persisting `oauth` on a URL-less row is what created the frontend's second
broken authorize button.

## 3. B1 — the duplicate/broken control authorize path

- **`"Server has no URL; OAuth is only for HTTP transports."`** originates at **`views.py:2526`** inside
  `MCPServerOAuthStartView.post` (`:2513`, route `servers/{pk}/oauth/authorize/` `urls.py:19`).
- ⚠ **Real backend inconsistency (not just a UI nuisance):** that view writes `server.auth_type="oauth"`
  **directly** (`views.py:2582`), guarded only by `server.url` present (`:2524`) — it does **NOT**
  re-apply the `serializers.py:177` transport guard. A websocket server with a `ws://` URL could be
  flipped to `oauth` via this path, outside the registration validation envelope.
- This control-side OAuth path is **redundant** with the gateway's per-org upstream OAuth proxy
  (`mcp_oauth_proxy.py` `oauth_start` / `oauth_callback`, mapped in item #1). The frontend
  (`MCPConnectorPanel.jsx:722`) already flags this as "that dup/broken" path.
- **B1 fix (item #13):** frontend only exposes oauth for HTTP transports and renders exactly ONE
  Authorize button (the gateway path); the control `MCPServerOAuthStartView` should be removed (or
  brought under the same transport guard) so `views.py:2526` "Server has no URL" is unreachable.

## 4. B2 — oauth_authorized + when tools appear

- Register → `tools_count=0`, no tools (`views.py:764`; tool rows are read-only via API — `serializers.py:297` — only sync creates them).
- OAuth callback (`MCPOAuthCallbackView.get`, `views.py:2658`) → `_store_oauth_tokens` (`:298`/`:2697`)
  sets `auth_token` → `oauth_authorized=True`, but **does NOT trigger a sync** (HTML: "You can sync its tools").
- Tools appear **only** after `POST /servers/{pk}/tools/` (`MCPServerToolListView.post:1889` →
  `_resync_server_tools:507` → `_discover_tools_via_gateway:407`).
- For a fresh **unauthorized** oauth server, sync yields **0 tools + an error** (not a silent empty):
  `_ensure_oauth_token_fresh` (`:331`) returns False → `_discover_tools_via_gateway` short-circuits to
  `([], "needs re-authentication")` (`:448/452`); `_resync_server_tools` sets `connection_status="failed"`,
  `needs_reauth=True` (`:554`). `GET /tools/` (`:899`) reads local inventory only (empty until a sync).
- **So tool appearance is gated on BOTH `oauth_authorized` AND a manual/cron sync.** The backend already
  exposes the right signal (`oauth_authorized`, `needs_reauth`, `connection_status`). **B2 fix (item #15):**
  frontend must render a distinct "Pending authorization" card for `auth_type=="oauth" && !oauth_authorized`
  (never a normal 0-tools card), and ideally auto-trigger a resync after the OAuth callback completes.

## 5. B3 — provisioning triggers (absent here)

Neither register nor authorize provisions the per-org broker sandbox:
- Register only auto-provisions a data-plane `GatewayAPIKey` (`views.py:810/821`) — **not** a sandbox.
- Authorize only does upstream DCR (`oauth.py:237` @ `views.py:2555`).
- `signals.py` post_save handlers **only** `bump_scan_version` (Redis INCR for gateway cache
  invalidation, `:86/:154`); bookkeeping saves (token/oauth/health/sync-timestamp) are **excluded**
  from `_SERVER_RELEVANT_FIELDS` (`:167`) so token acquisition triggers no gateway-visible change.
- `tasks.py` has ONE Celery task — `record_mcp_event_task` (`:12`), an audit-event writer — **no resync task.**
- The sandbox is provisioned lazily by the gateway/broker on the first discover-tools / tools-call
  round-trip. **B3 fix (item #19):** add eager provisioning on register/authorize/first-sync (control
  → gateway `ensure`), so the first real tool call never races a cold sandbox.

## 6. Integration points (control → gateway / upstream)
- Tool discovery: `requests.post` gateway `/v1/mcp/internal/discover-tools` with `X-Gateway-Internal-Key`, timeout 150s (`views.py:465`); tool exec: `/v1/mcp/internal/tools-call` timeout 90s (`:651`). `GATEWAY_URL` default `http://gateway:8300` (`:420`).
- Gateway-internal reads: `MCPGatewayEnabledToolsView` (`:2040`, enable/disable + scan_action), `MCPGatewayRecordEventView` (`:2152`, audit), `MCPGatewayNeedsReauthView` (`:2267`, Flow-2 stdio reauth backprop) — gated by `IsGatewayInternalOnly`/`_valid` internal key.
- Upstream OAuth AS: `oauth.py` `discover:169`/`register_client:237`/`exchange_code:321`/`refresh_access_token:342`/`_token_request:296` — all SSRF-re-guarded, `follow_redirects=False` (B10 fix), RFC 8707 resource param.
- Encryption: `auth_token`/`auth_password`/`auth_header_value`/`oauth_client_secret`/`oauth_refresh_token`/`oauth_code_verifier` are Fernet-encrypted at rest (`models.py:141-166`).

## Verification
Spot-verified (all exact): `serializers.py` 7,121,127,132,140,145,158,177,190,245; `models.py` 84,178,185,196,201,156;
`views.py` 298,331,384,507,540,764,784,810,821,1889,1906,2251,2267,2503,2513,2524,2526,2544,2582,2609,2658,2672,2697;
`oauth.py` 169,237,296,321,342; `signals.py` 86,154,167; `tasks.py` 12; `urls.py` 19,20. Frontend B1 comment: `MCPConnectorPanel.jsx:722`.
