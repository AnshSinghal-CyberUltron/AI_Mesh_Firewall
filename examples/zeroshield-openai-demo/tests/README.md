# Frontend tests (Playwright)

Runs against a live demo backend — compose serves it on `:8770` (the script's default).

**Credentials are required.** Every demo data route is behind a control-plane login, so
the suite signs in the way a customer does. Set `DEMO_EMAIL` / `DEMO_PASSWORD` (or
`TEST_EMAIL` / `TEST_PASSWORD`); the script refuses to run without them rather than
reporting a misleading pass.

## Quick (standalone script)
```bash
# from examples/zeroshield-openai-demo
npx playwright install chromium-headless-shell      # once, if not already present
DEMO_URL=http://127.0.0.1:8770 \
DEMO_EMAIL=you@company.com DEMO_PASSWORD=... \
NODE_PATH="$(npm root -g)" node tests/playwright_demo.cjs
```
`NODE_PATH` must point at a `node_modules` containing the `playwright` package whose
version matches the installed browser build — a mismatch fails with
"Executable doesn't exist at …/chromium_headless_shell-<rev>".

Exits non-zero on any failure. Prints a ✅/❌ line per check.

## As a @playwright/test project
```bash
npm init -y && npm i -D @playwright/test && npx playwright install chromium
DEMO_URL=http://127.0.0.1:8770 DEMO_EMAIL=… DEMO_PASSWORD=… \
  npx playwright test tests/playwright_demo.cjs
```
The script is written so it also runs standalone; to use the `@playwright/test`
runner, wrap the body in `test('demo e2e', async ({ page }) => { ... })`.

## What it covers (customer perspective)
- console login (control-plane JWT + org simulator key)
- gateway connection banner
- chat (non-stream) + 9-stage pipeline visualizer
- output validation: PII → redact, prompt-injection → block (visualized, not a crash)
- RAG ingest + grounded query
- MCP context-grounded answer
- routing visualizer (served model)
- governance-signals strip (quota / clamp / review response headers), including the
  case where the gateway sent none — an empty strip must not read as "all clear"
- MCP scan posture is disclosed, not implied (`tag` detects but does not enforce)
- page reload reconnects
- zero console errors

Last measured run: **15 passed, 0 failed** — 2026-07-22, against compose on `:8770`
with the gateway rebuilt from source. Re-measure after changes rather than trusting
this line; it is a record of one run, not a guarantee.
