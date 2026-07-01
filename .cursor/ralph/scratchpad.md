---
iteration: 2
min_iterations: 20
max_iterations: 50
completion_promise: "COMPLETE and tested from frontend and backend"
status: ACTIVE
active_story: E1-openai-sdk-frontend-playwright
prd: scripts/ralph/prd.json
note: Ralph OpenAI SDK campaign — E1 live gates blocked by Docker contention
---

# E1 OpenAI SDK Frontend Playwright — Ralph Loop

## Iteration playbook

Follow `scripts/ralph/CLAUDE.md`. Story: **E1-openai-sdk-frontend-playwright**.

## Gates (all required for passes:true)

1. `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_openai_sdk_compat.py -q && ./.venv/bin/python -m pytest ai_mesh_gateway/tests -q`
2. `cd frontend && npm run build`
3. `docker compose --profile services up -d` + health :8180 :8100 :8300
4. Playwright: `cd frontend && BASE_URL=http://127.0.0.1:8180 E2E_REPORT=../runs/playwright_demo_simulators.json node ../scripts/playwright_demo_simulators.mjs`
5. Live SDK: `cd gateway && GATEWAY_API_KEY=<sim-key> ./.venv/bin/python ../scripts/openai_sdk_live_gateway.py`

## Iteration log

- **2026-06-30 iter 1** (prior): SDK compat PASS; build PASS; full pytest FAIL (33 drift+MCP); docker duplicate-control; live gates NOT RUN.
- **2026-06-30 iter 2**: Removed duplicate control containers; force-recreate cycles. Gates: (#1a) SDK compat **PASS** 46+2xp; (#1b) full pytest **FAIL** 996 pass / 14 fail / 13 err (MCP sandbox needs :8311 broker + context_guard drift); (#2) frontend build **PASS**; (#3) stack **FLAP** — control healthy then wedges (telemetry saturation); gateway/frontend stuck `Created` / "marked for removal" from parallel compose races; (#4) playwright **FAIL** 15/15 asserts through rag-sim — isolation Execute not visible (fix: scroll+anchor Isolation Operations Simulator card); (#5) live SDK **FAIL** chat allow timeout (gateway config-reload storm). E1 passes:false. Next: Docker Desktop restart + sole compose owner; poll auth once (avoid 429); run live gates in clean window after gateway startup complete.
