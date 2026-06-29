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
