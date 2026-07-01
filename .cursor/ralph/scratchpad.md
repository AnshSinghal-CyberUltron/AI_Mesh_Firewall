---
iteration: 2
min_iterations: 20
max_iterations: 50
completion_promise: "COMPLETE and tested from frontend and backend"
status: ACTIVE
active_story: E1-openai-sdk-frontend-playwright
prd: scripts/ralph/prd.json
campaign: openai-sdk-e1
note: Ralph iter 2 — offline gates green; live gates blocked by Docker daemon corruption
---

# OpenAI SDK E1 — Cursor Ralph Loop

Campaign `openai-sdk-e1`. Story E1-openai-sdk-frontend-playwright (passes:false).

## Gates (full order)
1. `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_openai_sdk_compat.py -q && ./.venv/bin/python -m pytest ai_mesh_gateway/tests -q`
2. `cd frontend && npm run build`
3. `docker compose --profile services up -d` + health :8180 :8100 :8300
4. `cd frontend && BASE_URL=http://127.0.0.1:8180 E2E_REPORT=../runs/playwright_demo_simulators.json node ../scripts/playwright_demo_simulators.mjs`
5. `cd gateway && CONTROL_URL=http://127.0.0.1:8100 GATEWAY_URL=http://127.0.0.1:8300 ./.venv/bin/python ../scripts/openai_sdk_live_gateway.py`

## Iteration log

### Iteration 1 — 2026-06-30
- WIP: offline gates mostly green; docker unreachable; live gates not run.

### Iteration 2 — 2026-07-01
- GREEN: test_openai_sdk_compat.py (46 passed, 2 xpassed); full gateway pytest (1005 passed); frontend build.
- PARTIAL live: playwright 10/10 asserts through attack PII scenario, failed clean-prompt Run Pipeline click (SPA navigation race under control wedge).
- PARTIAL live: openai_sdk_live_gateway.py 3/6 steps (login/key/model OK; chat allow Connection error).
- BLOCKED: Docker daemon corruption after compose down/up — stale container IDs, "marked for removal", duplicate control ghosts. Requires Docker Desktop restart before live gates can complete.
- FIXES: playwright runAttackScenario re-resolves panel + waitFor Run Pipeline; live SDK chat retries 4→8.
