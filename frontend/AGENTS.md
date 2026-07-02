# frontend/ — conventions & gotchas

## Dev stack
- Vite dev server runs in Docker, host-mapped to **:8180** (container 5173). It proxies `/api` →
  control plane (:8100) and `/v1` → gateway (:8300). chromadb test store is :8001.
- Login: `POST /api/auth/token/` (NOT `/api/auth/login/`, which 404s). Body `{email, password}` →
  `{access, refresh}`. Tokens persist in `localStorage` keys `auth_access` / `auth_refresh`
  (see `src/context/AuthContext.jsx`). `me` from `GET /api/auth/me/`. Logout: `POST /api/auth/logout/`
  then `clearStoredTokens()`.
- Dev admin: `admin@zeroshield.io` / `Adm1n!Pass#2024`
  (created by `control … manage.py ensure_zeroshield_admin`; dev-default password).

## Gates (there IS a lint script now)
- `npm run lint` === `npm run test:unit` (node --test over `src/utils/*.test.js`). `npm run build`
  is the compile gate. Browser behavior is verified by the durable `scripts/playwright_*.mjs` runners
  (run node from THIS dir so `playwright` resolves; pass `SHOT_DIR`/`E2E_REPORT` env to write artifacts
  to repo-root `runs/`). Example: `scripts/playwright_auth_keys.mjs` (D1: auth flows + GatewayKeyPanel).

## Auth / key UI gotchas
- `GatewayKeyPanel` (`src/components/GatewayKeyPanel.jsx`, mounted on the firewall-1-1 + firewall-1-5
  tabs): create returns the full secret **once** in a modal ("will not be shown again"); the listing
  thereafter shows only `prefix...` + an Active/Revoked badge. The full secret must NEVER be
  re-rendered in the table (honesty invariant — assert it's absent from the DOM).
- **Revoke = hard DELETE** `/api/gateways/keys/{id}/` → the row leaves the listing. The red "Revoked"
  badge is only for retained-but-inactive keys (e.g. expired), NOT the post-revoke state.
- Gateway key IDs are **UUIDs** — a DELETE-URL matcher in tests must use `[^/]+`, not `\d+`.
- There is **no dedicated rotate** control/endpoint; rotation == revoke + create.
- Inactivity auto-logout (`src/hooks/useInactivityLogout.js`, wired in `DashboardLayout.jsx`) is
  hardcoded 15m logout / 14m warning, polled every 30s off `Date.now()`. In Playwright verify it with
  `page.clock.install()` + `fastForward(14*60e3+ε)` AFTER `networkidle` (so the login fetch's
  `AbortSignal.timeout` has already resolved under real time).
- `/oauth/callback` (`src/pages/OAuthCallback.jsx`) intentionally preserves `?code`/`?state` (the
  router `*` route would otherwise redirect to `/` and destroy them) and only shows a spinner; token
  exchange happens in the opener window (MCPManagerPanel).

## MCP panels (Module 1.4 — Context Assembly & MCP)
- The 1.4 control surface is ONLY `MCPConnectorPanel` (`src/components/MCPConnectorPanel.jsx`); it
  mounts the registry + 6 internal Tabs: MCP Servers, Tool Discovery, Tool Execution, Scan Controls
  (`MCPScanControlMatrix`), MCP Security Policies (`PolicyManagementPanel`), Observability.
  `MCPManagerPanel`/`MCPScannerPanel` are NOT wired into any live route — not customer-reachable.
- Durable verifier: `scripts/playwright_mcp_panels.mjs` (D4). UI honesty assertions: server-card count
  (== count of `Delete server` aria-buttons) == GET `/api/mcp-connector/servers/` length; per-card
  "Connected" badges (`span.rounded-md`, NOT the header `span.rounded-full border` pill) == backend
  connected count; Tool Execution decision badge ("Allow" via `lib/mcpColors.js` DECISION) mirrors the
  POST `/api/mcp-connector/tools/call/` `decision`.
- Register CRUD in tests: prefer transport=**stdio** (command `npx` + args). streamable-http register
  is gated by an SSRF guard that requires DNS resolution to succeed → public hosts 400 from inside the
  control container. tool-call request body is `{name, arguments, server_slug}` (NOT `tool_name`).

### MCP OAuth bug sites (B1/B2/B4) — current state (map: `docs/mcp/frontend-panel-flow.md`)
These carry substantial PRIOR fixes — treat as verification-first, don't blind re-implement.
- **B1 (oauth+stdio / one Authorize button / "Server has no URL"):** form filters the `oauth`
  auth-type option to `streamable-http`/`sse` only (`:1483-1490`) and drops oauth→none on transport
  switch (`:1385-1392`). Cards show exactly one Authorize via mutually-exclusive helpers:
  `serverNeedsOAuth` (`:711`, stdio mcp-remote) → `startOAuth` (`:773`, **gateway** `oauth/start`) vs
  `serverUsesHttpOAuth` (`:726`, http-oauth+url) → `startControlOAuth` (`:845`, **control**
  `servers/{id}/oauth/authorize/`). `serverUsesHttpOAuth` requires `!!srv.url` so the button never
  surfaces "Server has no URL". REMAINING: the control path (`startControlOAuth` → backend
  `MCPServerOAuthStartView`, `views.py:2513/2526/2582`) is the dup/broken path B1 wants deleted.
- **B2 (fresh oauth server 0-tools card):** `serverAwaitingAuth` (`:741`) renders the
  `"authorization required"` badge (`:1095-1097`); `syncBlockedForAuth` (`:757`) disables sync until
  authorized. Backend signal = `oauth_authorized` (`control models.py:178`). Verify UX reads as pending.
- **B4 (modal focus loss):** ADDRESSED at code level — no component-defined-inside-render (render
  helpers are function CALLS `{renderServers()}` `:2203-2208`, not `<Comp/>`); `ui/Dialog.jsx` keeps
  `onClose` in a ref so the focus-trap effect (deps `[open, handleKey]`, `handleKey`=`useCallback([])`)
  runs only on open-toggle, not per keystroke. Verify with Playwright: type a long string per field.

## Module-1 live simulators (D6) — verdict honesty
- A gateway policy/CONTENT block is **HTTP 400 `code=content_filter`** on this gateway (OpenAI-compat
  content-block status is on), NOT 403. Any verdict mapping that keys "block" off 403-only mislabels it.
  `liveGateway.inferFinalAction` now treats a 4xx with the gateway's block markers (code/err.code in
  {content_blocked,content_filter,blocked}, `blocked_by`!=rate_limit, category includes policy/violation)
  as `block` before the generic 4xx→error fallthrough; a plain validation 400 still → error. Keep that
  invariant: a firewall verdict must win over "it's a 4xx so it's an error". `/v1/rag/query` blocks ARE
  403 (separate handler).
- Gateway error bodies carry `error` as a STRING **or** a nested OpenAI object `{message,type,param,code}`.
  Never render `{body.error}` directly — React throws "Objects are not valid as a React child" and crashes
  the result subtree (a real block then shows NOTHING). Coerce with a helper (string→as-is,
  object→`.message||JSON.stringify`). See `errorToText` in RAGAttackTrustSimulator.jsx.
- Simulators auto-provision a per-org gateway key: AttackSim/Isolation via `useSimulatorEngine`
  (`POST /api/gateways/simulator-default/`, localStorage `zeroshield_gateway_key:{orgId}`), RAG via
  `useGatewayCredential` (shows an "Auto key" chip when ready). AttackSim/Isolation also require ≥1
  *eligible* model (active + usable key) from `/api/firewall/models/`. For a deterministic browser gate,
  pre-seed `zeroshield_gateway_key:{orgId}` + `zeroshield_simulator_model` in localStorage before nav.
- Bedrock/guard-model test panel = `ZeroShieldGuardModelTestPanel` on `?tab=firewall-config`
  (`ZeroShieldTestCard`), POSTs `/api/admin/gateway/bedrock-test/` with `{check_health:true}` or `{prompt}`.
  Buttons: "Check Health" / "Send Test"; result cell "Recommended Action" = block|flag|allow.
- Durable gate: `scripts/playwright_demo_simulators.mjs` (run from frontend/). Double-checks each verdict:
  `waitForResponse` captures the real API status AND asserts the on-screen label; raw PII never rendered.
