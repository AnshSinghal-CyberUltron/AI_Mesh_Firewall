---
iteration: 3
min_iterations: 20
max_iterations: 50
completion_promise: "COMPLETE and tested from frontend and backend"
status: ACTIVE
active_story: E1-openai-sdk-frontend-playwright
prd: scripts/ralph/prd.json
campaign: openai-sdk-frontend
note: E1 all gates green iter3 — chroma up + disabled duplicate pinecone docs policy
---

# OpenAI SDK Frontend — Cursor Ralph Loop

Campaign `openai-sdk-frontend`. Story **E1-openai-sdk-frontend-playwright** passes:true (iter 3).

## Gates reference

**E1:** backend test_openai_sdk_compat.py + full pytest; frontend build; playwright_demo_simulators.mjs; live_gateway_sdk.py

**Stack:** Vite :8180, control :8100, gateway :8300 — login admin@zeroshield.io / Adm1n!Pass#2024

## Iteration log

### Iteration 3 — 2026-07-01
- Infra: aimesh_gate stack healthy; chromadb profile (aimesh_gate-chromadb-1 :8001); competing ralph.sh PIDs 33458/33544 noted (not started).
- RAG 422 root cause: duplicate org-3 `docs` vector policies — seed pinecone/deny overwrote custom/chroma in compile (same `{org_id}::{collection}` key); disabled 535ed962 pinecone policy, recompiled → `3::docs custom allow`.
- Gates ALL GREEN: test_openai_sdk_compat 46p/2xp; full pytest 1005p; frontend lint+build OK; stack :8180/:8100/:8300; playwright_demo 23/23; live_sdk 6/6 PASS.

### Iteration 4 — 2026-07-01 (prior campaign)
- O3/O4 Playwright gates green on aimesh_gate stack.
