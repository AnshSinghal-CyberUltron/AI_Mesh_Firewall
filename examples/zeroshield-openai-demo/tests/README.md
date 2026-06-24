# Frontend tests (Playwright)

Two ways to run, both against a live demo backend on `:8800`.

## Quick (zero-config, isolated chromium)
```bash
# from examples/zeroshield-openai-demo
NODE_PATH=/opt/homebrew/lib/node_modules node tests/playwright_demo.cjs
```
Exits non-zero on any failure. Prints a ✅/❌ line per check.

## As a @playwright/test project
```bash
npm init -y && npm i -D @playwright/test && npx playwright install chromium
DEMO_URL=http://127.0.0.1:8800 npx playwright test tests/playwright_demo.cjs
```
The script is written so it also runs standalone; to use the `@playwright/test`
runner, wrap the body in `test('demo e2e', async ({ page }) => { ... })`.

## What it covers (customer perspective)
- gateway connection banner
- chat (non-stream) + 9-stage pipeline visualizer
- output validation: PII → redact, prompt-injection → block (visualized, not a crash)
- RAG ingest + grounded query
- MCP context-grounded answer
- routing visualizer (served model)
- page reload reconnects
- zero console errors

Latest run: **12 passed, 0 failed**.
