---
iteration: 1
min_iterations: 20
max_iterations: 50
completion_promise: "MCP FRONTEND ADVERSARIAL COMPLETE"
status: ACTIVE
active_story: R1-regression-iter5
prd: scripts/ralph/prd-mcp-frontend-adversarial.json
note: Cursor Ralph Loop — NOT Claude CLI. FULL RESET 2026-06-30.
---

# MCP Frontend Adversarial — Cursor Ralph Loop

You are one **Cursor Agent** iteration of the Ralph loop. The **stop hook** in `~/.cursor/plugins/local/ralph-loop/` re-feeds this prompt after each turn until you output the completion promise.

## User requirements (full)

- **Min 20 iterations** (`min_iterations: 20` in frontmatter)
- **Each iteration:** run **2 parallel org agents** (`adv-org-alpha`, `adv-org-beta`), each with **≥5 MCP servers** in Docker sandbox
- **Leakage checks** + **adversarial break-sandbox probes**
- **Frontend Playwright** — `MCPConnectorPanel` + all MCP server presets
- **Gates per iteration** (run in order; fix failures before next gate):
  1. `docker compose --profile services up -d` (ensure healthy)
  2. `node scripts/ralph/run_parallel_org_agents.mjs --full`
  3. `make mcp-adversarial-gate` OR equivalent pytest + Playwright
  4. Mark **one** PRD story `passes:true` when all gates green
- Read `scripts/ralph/prd-mcp-frontend-adversarial.json` **each iteration**
- **DO NOT** use `claude` CLI or `scripts/ralph/ralph.sh`
- Only output `<promise>MCP FRONTEND ADVERSARIAL COMPLETE</promise>` when **ALL** stories pass **AND** `iteration` ≥ 20

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

Output `<promise>MCP FRONTEND ADVERSARIAL COMPLETE</promise>` only when all stories pass AND iteration ≥ 20.

---

## Iteration log

### FULL RESET 2026-06-30 (user restart — Cursor Ralph)
- `make mcp-adversarial-reset` + `mcp-adversarial-reset-prd`; iteration: 1; R1–R16 `passes:false`.
- Docker stack up; starting **iteration 1** gates (2× org parallel agents → pytest → frontend E2E).
- **Active story:** R1-regression-iter5

### Iteration 1 attempt (2026-06-30) — gates NOT fully green
- **Gate 1** (`run_parallel_org_agents.mjs --full`): GREEN on best run (2×6 servers, cross-org leak, 4 leakage pytest)
- **Gate 2** (full pytest): 11 passed / 2 failed (`test_parallel_2_orgs_5_servers_tools_list`, `test_network_concurrent_cross_org_rpc_hammer` — stdio limit 8); fix applied `MCP_STDIO_MAX_PROCESSES_PER_ORG=16`
- **Gate 3** (frontend Playwright): not reached
- **R1** remains `passes:false`; **next iteration:** retry full `make mcp-adversarial-gate` after `docker compose build mcp-broker` + fresh sandboxes
