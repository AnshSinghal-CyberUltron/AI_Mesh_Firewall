# Visual + behavior regression gate

Codifies the hardening invariants proven during the frontend-harden loop into an
automated, re-runnable check across **every surface × BOTH themes × 4 widths**
(1440 / 1024 / 768 / 375). It is the regression gate for items 1–22 of
`scripts/ralph/frontend_harden_scratchpad.md`.

## What it gates (per surface × theme × width)

| Invariant | Check | Fails the gate when |
|---|---|---|
| **Responsive** | `main.scrollWidth − main.clientWidth`, then the real offenders that extend past the viewport (excluding elements inside `overflow-hidden`/`auto`/`scroll` ancestors and transformed nodes) | any real horizontal overflower exists |
| **No leak** | regex scan of DOM text + every input value for `sk-…`, `AKIA…`, `pcsk_…`, `ghp_…`, PEM, JWT | any match |
| **Stability** | uncaught `pageerror` + non-network `console.error` | a page error or real JS console error |
| **Charts** | ECharts canvases vs recharts SVGs on `migratedChart` surfaces | any recharts SVG (migration regressed) |
| **Auth/theme** | authenticated surfaces render `<main>` in both themes | `<main>` missing (token bad / backend down) |

> Why `mainScroll` and not `documentElement.scrollWidth`? The app shell's `<main>`
> has `overflow-y-auto`, so CSS computes its `overflow-x` to `auto` and it silently
> **absorbs** horizontal overflow into an internal scrollbar — a `documentElement`
> scan reads `0` and misses it. See FRONTEND_AUDIT.md item 22.

Network 5xx / failed-resource console noise (the transient dev-DB "too many clients"
pressure, aka F3) is reported but **not** gated, so the check is robust to that
environmental flake while still catching real regressions.

Each combo also writes a PNG to `__output__/screenshots/` (visual artifact for human
review) and a structural row to `__output__/report.json` (behavior snapshot). Both are
git-ignored — the gate is threshold-based, not a brittle pixel/JSON baseline.

## Prerequisites

- The docker dev stack up: frontend on `:8180`, `ai_mesh_firewall-control-1` healthy.
- A resolvable Playwright + Chromium. Playwright is intentionally **not** a project
  dependency (keeps the shared main branch's `npm ci` light). `audit.mjs` resolves it
  from, in order: `PLAYWRIGHT_PATH`, `frontend/node_modules/playwright`, the npx cache.
  To install locally: `npm i -D playwright && npx playwright install chromium`.

## Run

```bash
# mints a JWT from the control container, runs all surfaces:
tests/visual/run.sh
# or from npm:
npm run test:visual

# a subset:
VISUAL_ONLY=overview,module-1-3 tests/visual/run.sh

# bring your own token / base url:
VISUAL_TOKEN=eyJ... VISUAL_BASE_URL=http://127.0.0.1:8180 node tests/visual/audit.mjs
```

Exit code is non-zero on any gated regression; the failing combos are printed.

### Tuning / F3 (dev-DB saturation)

The dev Postgres pool is small; loading the full app ~100× back-to-back can exhaust it
(the "too many clients" flake, aka F3), after which the session check hangs and the app
falls back to login — the gate reports this as `AUTH_FAILED (no <main>)`. The harness
already re-navigates with backoff (up to 5×) and pauses between combos, but on a loaded
box you may still hit it. If `AUTH_FAILED` appears:

- run smaller batches: `VISUAL_ONLY=overview,module-1-3 tests/visual/run.sh`
- raise the inter-combo pause: `VISUAL_PAUSE=1500 …`
- give each load longer to settle: `VISUAL_SETTLE=5000 …`

`AUTH_FAILED` reflects backend capacity, not a frontend regression. A CI runner with an
adequately-sized DB pool runs the full matrix without it.

## Companion browserless gate

`src/utils/hardening-regression.test.js` (runs in `npm test` / `npm run lint`, no
browser or backend) statically locks in the specific source-level fixes from this loop
— chart migration, the responsive fixes, and theme-contrast fixes — so they can't be
silently reverted even without running the live harness.
