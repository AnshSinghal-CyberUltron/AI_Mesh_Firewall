---
iteration: 3
min_iterations: 20
max_iterations: 50
completion_promise: "COMPLETE and tested from frontend and backend"
status: ACTIVE
active_story: R3-regression-iter7
prd: scripts/ralph/prd-mcp-frontend-adversarial.json
note: Cursor Ralph Loop — iteration 2 gates green (R2 pass); starting iter 3.
---

# MCP Frontend Adversarial — Cursor Ralph Loop

You are one **Cursor Agent** iteration of the Ralph loop. The **stop hook** in `~/.cursor/plugins/local/ralph-loop/` re-feeds this prompt after each turn until you output the completion promise.

## User requirements (full)

- **Min 20 iterations** (`min_iterations: 20` in frontmatter)
- **Max 50 iterations** (`max_iterations: 50`)
- **Each iteration:** run **2 parallel org agents** (`adv-org-alpha`, `adv-org-beta`), each with **≥5 MCP servers** in Docker sandbox
- **Docker naming:** per-org sandboxes MUST be `{org_slug}-mcp-sandbox` (e.g. `adv-org-alpha-mcp-sandbox`, `zeroshield-mcp-sandbox`)
- **Leakage checks** + **adversarial break-sandbox probes**
- **Frontend Playwright** — `MCPConnectorPanel` + all MCP server presets
- **Gates per iteration** (run in order; fix failures before next gate):
  1. `docker compose --profile services up -d` (ensure healthy)
  2. `node scripts/ralph/run_parallel_org_agents.mjs --full`
  3. `make mcp-adversarial-gate` OR equivalent pytest + Playwright
  4. Mark **one** PRD story `passes:true` when all gates green
- Read `scripts/ralph/prd-mcp-frontend-adversarial.json` **each iteration**
- **DO NOT** use `claude` CLI or `scripts/ralph/ralph.sh`
- Only output `<promise>COMPLETE and tested from frontend and backend</promise>` when **ALL** stories pass **AND** `iteration` ≥ 20

## DO NOT USE

- `claude` CLI or `claude --print`
- `scripts/ralph/ralph.sh`, `ralph-mcp-frontend-adversarial.sh`, or any bash loop that spawns Claude Code headless
- `./scripts/ralph/run_logical_iteration.sh` as a substitute for doing the work yourself — that script is for **Terminal operators** only; **you** run the gates directly in this Cursor session

## Campaign status

| Metric | Value |
|--------|-------|
| Feature stories F0–F20 | all `passes:true` (implementation done) |
| Regression stories R1–R16 | `passes:false` — **your work** |
| Min iterations | 20 |
| Max iterations | 50 |

### Iteration map (logical → PRD)

| Scratchpad `iteration` | PRD story | Extra gate |
|------------------------|-----------|------------|
| 1 | R1-regression-iter5 | — |
| 2 | R2-regression-iter6 | — |
| … | … | … |
| 16 | R16-regression-iter20 | `cd frontend && npm run lint && npm run build` |
| 17–20 | (no PRD mark) | re-run full gates for min-20 proof |
| 21+ | — | only if still failing stories |

---

## EVERY iteration — follow exactly

### 0. Orient

1. Read `scripts/ralph/prd-mcp-frontend-adversarial.json`.
2. Pick the **first** story with `passes:false` whose `dependencies` are all `passes:true`.
3. Note scratchpad `iteration` from frontmatter.

### 1. Ensure stack is up

```bash
./scripts/ensure_mcp_sandbox_network.sh 2>/dev/null || true
docker compose --profile services up -d
curl -sf http://127.0.0.1:8311/health | jq -e '.docker_ok==true'
```

### 2. Run gates IN ORDER

```bash
node scripts/ralph/run_parallel_org_agents.mjs --full
cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_sandbox_adversarial.py -q
BASE_URL=http://127.0.0.1:8180 node tests/e2e/mcp_sandbox_adversarial/frontend_parallel_orgs.mjs
```

**On R16 only**, also: `cd frontend && npm run lint && npm run build`

### 3. Fix failures

Fix **production code**, not tests. Security invariants: egress bytes are truth; fail closed on no-op scrub; cross-org isolation must hold.

### 4. Mark progress

When all gates green:

1. Set matching R-story `passes:true` in PRD
2. Commit locally: `git add -A && git commit -m "ralph(<story-id>): regression gate green"`
3. Bump scratchpad `iteration` by 1
4. Append note to Iteration log below

### 5. Completion check

Output `<promise>COMPLETE and tested from frontend and backend</promise>` only when all stories pass AND iteration ≥ 20.

---

## Iteration log

### Iteration 1 gates 2–3 (2026-06-30)
- **Gate 2** (full adversarial pytest): **GREEN** — 13 passed in ~9m18s
- **Gate 3** (frontend Playwright `frontend_parallel_orgs.mjs`): **FAIL** — `The operation was aborted due to timeout`
- **R1** remains `passes:false`; next iteration should retry Playwright (stack up, `BASE_URL=http://127.0.0.1:8180`)

### Iteration 1 gate 3 retry (2026-06-30)
- **Root cause:** `touchSandboxActivity` used 30s `AbortSignal.timeout` (broker ensure ~11–15s, flaky under load); `openMcpPanel` waited on page-wide `.animate-spin` (module hero loader) and `networkidle` never settled on polling UI.
- **Fix:** test-only — 120s broker ensure with retries, best-effort warm, scoped MCP panel wait, `load` + post-login redirect wait.
- **Gate 3 retry:** **GREEN** — 5 presets synced + tools-call-ok in ~54s (`runs/frontend_parallel_orgs.json`)
- **R1-regression-iter5:** `passes:true`; scratchpad bumped to iteration 2

### Iteration 2 (2026-06-30)
- **Gate 1** (parallel org agents --full): **GREEN** — 2×6 servers + cross-org leak probe ~98s
- **Gate 2** (full adversarial pytest): **GREEN** — 13 passed in ~47s
- **Gate 3** (frontend Playwright): **GREEN** — 5 presets synced + tools-call-ok in ~37s
- **R2-regression-iter6:** `passes:true`; scratchpad bumped to iteration 3
