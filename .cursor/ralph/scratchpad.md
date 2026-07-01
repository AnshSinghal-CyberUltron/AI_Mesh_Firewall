---
iteration: 3
min_iterations: 20
max_iterations: 50
completion_promise: "COMPLETE and tested from frontend and backend"
status: ACTIVE
active_story: O3-frontend-streaming-sse
prd: scripts/ralph/prd-openai-sdk-frontend.json
campaign: openai-sdk-frontend
note: OpenAI SDK frontend testing via ruflo + Playwright — Cursor multitask Ralph
---

# OpenAI SDK Frontend — Cursor Ralph Loop

You are one **Cursor Agent** iteration of the Ralph loop for campaign `openai-sdk-frontend`.

## DO NOT USE

- `claude` CLI or `claude --print`
- `scripts/ralph/ralph.sh`, `ralph-openai-sdk-loop.sh`, or bash loops spawning Claude Code headless

## Iteration playbook

1. Read this scratchpad + `scripts/ralph/prd-openai-sdk-frontend.json`
2. Read `scripts/ralph/progress.txt` (## Codebase Patterns)
3. Pick the SINGLE highest-priority story with `passes:false` whose dependencies all pass
4. Run REAL gates; fix failures; re-run until green
5. Local commit: `ralph(<story-id>): <title>`
6. Set story `passes:true` in PRD; bump `iteration`; append log below
7. Ruflo: `memory_search` before work; `memory_store` after green gate

## Gates reference

**Backend (O1 scoped — full pytest NOT required for O1):**
```bash
cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_openai_sdk_compat.py -q
cd gateway && ./.venv/bin/python ../scripts/openai_sdk_live_gateway.py
# When control wedged: GATEWAY_URL=http://127.0.0.1:8300 GATEWAY_API_KEY=<simulator-key>
```

**Frontend:**
```bash
cd frontend && npm run lint && npm run build
NODE_PATH=$PWD/tests/e2e/node_modules BASE_URL=http://127.0.0.1:8180 \
  E2E_REPORT=runs/playwright_openai_sdk.json node scripts/playwright_openai_sdk.mjs
```

**Stack:** Vite :8180, control :8100, gateway :8300 — login admin@zeroshield.io / Adm1n!Pass#2024

## Stack recovery (shared with MCP campaign)

- Poll 8180/8100/8300 health; `docker compose --profile services up -d control gateway frontend mcp-broker`
- Control can flap after cold start (~13–14 min recovery observed iter3); retry Playwright with `NODE_PATH=$PWD/tests/e2e/node_modules`

## Iteration log

### Iteration 1 — 2026-06-30
- Created campaign PRD, Playwright gate, live SDK script
- **O0-bootstrap** green (health 200/200/200)
- **O1** partial: compat pytest + live script green; full pytest blocked by MCP sandbox docker failures
- **O2** Playwright pre-validated; blocked on O1 in PRD

### Iteration 2 — 2026-06-30
- Scoped O1 gate; compat pytest 46 passed, 2 xpassed; frontend lint/build green; blocked on stack/control hang

### Iteration 3 — 2026-06-30
- Poll ~14m until 8180/8100/8300 all 200 (<5s)
- **O1-backend-sdk-compat** `passes:true` — compat pytest + `openai_sdk_live_gateway.py` green
- **O2-frontend-chat-playwright** `passes:true` — lint/build + Playwright (NODE_PATH) all phases green
- Log: `scripts/ralph/cursor_ralph_openai_iter3.log`
