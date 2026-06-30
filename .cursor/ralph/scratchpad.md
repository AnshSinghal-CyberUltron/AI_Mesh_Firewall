---
iteration: 1
min_iterations: 20
max_iterations: 50
completion_promise: "MCP FRONTEND ADVERSARIAL COMPLETE"
status: ACTIVE
active_story: R1-regression-iter5
pending_regression: R1-R16 (logical iters 1-16)
note: FULL RESET 2026-06-30 — user requested restart from logical iter 1
---

# MCP Frontend Adversarial Sandbox Isolation — Ralph Loop

**Prior campaign:** `prd-mcp-sandbox.json` — 15/15 complete (backend sandbox MVP).

**This campaign:** Rigorous **frontend + adversarial** isolation testing with **2 parallel org agents per iteration**.

## Status (FULL RESET 2026-06-30)

| Metric | Value |
|--------|-------|
| Feature stories | **21/21** (F0–F20) `passes:true` — implementation complete |
| Regression stories | **R1–R16** reset to `passes:false` |
| Logical iteration | **1** (restart) |
| Min iterations | 20 |
| Max iterations | 50 |
| Completion | Valid only when F0–F20 **and** R1–R16 all `passes:true` |

### Iteration numbering

| Scratchpad logical iter | Regression iter | PRD story |
|-------------------------|-----------------|-----------|
| 1 | 5 | R1 |
| 2 | 6 | R2 |
| … | … | … |
| 16 | 20 | R16 (final + lint/build) |
| 17–20 | 21–24 | extra gate runs (no PRD mark) |

Run one iteration (foreground — avoids subagent SIGTERM):

```bash
./scripts/ralph/run_logical_iteration.sh <1-20>
```

Run all 20 logical iterations:

```bash
for i in $(seq 1 20); do ./scripts/ralph/run_logical_iteration.sh "$i" || exit 1; done
```

See `scripts/ralph/START_IN_TERMINAL.md` for full operator guide.

## Task prompt (EVERY iteration)

1. **FIRST** — 2 parallel org agents (each org ≥5 MCP servers in sandbox):
   ```bash
   node scripts/ralph/run_parallel_org_agents.mjs --full
   ```
   - `adv-org-alpha` and `adv-org-beta` run concurrently
   - Each: own sandbox, 6 stdio MCP servers, tools/list + tools/call, adversarial env probes
   - Cross-org leak probe runs in parallel with workers

2. **Backend adversarial pytest** (leakage, escape, denylist):
   ```bash
   cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_sandbox_adversarial.py -q
   ```

3. **Frontend Playwright** (MCPConnectorPanel — register 5+ presets per org, sync, tools/call):
   ```bash
   BASE_URL=http://127.0.0.1:8180 node tests/e2e/mcp_sandbox_adversarial/frontend_parallel_orgs.mjs
   ```

4. **Iter 16 / R16 only:** `cd frontend && npm run lint && npm run build`

5. Mark matching R-story `passes:true` in `prd-mcp-frontend-adversarial.json` only after all gates green.

## Requirements

| Rule | Value |
|------|-------|
| Min iterations | 20 |
| Max iterations | 50 |
| Completion promise | `MCP FRONTEND ADVERSARIAL COMPLETE` |
| Orgs per iteration | 2 (`adv-org-alpha`, `adv-org-beta`) |
| MCP servers per org | ≥5 stdio (harness uses 6) |
| Parallel gate | `node scripts/ralph/run_parallel_org_agents.mjs --full` |

## Architecture

- `mcp-broker` — Sandbox Controller; Docker socket on broker only.
- Per-org container (`ai_mesh.org_slug`); sandbox-agent on :9320 inside container.
- Gateway `MCP_STDIO_IN_PROCESS=false` → `mcp_sandbox_client.py` → broker.
- Frontend `MCPConnectorPanel` — stdio preset registration, tools/list sync, tools/call.

## Quality gates

### Stack up
```bash
./scripts/ensure_mcp_sandbox_network.sh 2>/dev/null || true
docker compose --profile services up -d
curl -sf http://127.0.0.1:8311/health | jq -e '.docker_ok==true'
```

### Makefile shortcuts
```bash
make mcp-adversarial-reset    # kill batches, reset locks, archive log
make mcp-adversarial-iter ITER=1   # one logical iteration in foreground
make mcp-adversarial-loop     # print / run 20-iter command
```

## Iteration log

### FULL RESET 2026-06-30
- Killed all regression/batch processes; cleared `.regression.lockdir` and `regression_batch.pid`.
- Archived `regression.log`; fresh log started.
- PRD: F0–F20 kept `passes:true`; R1–R16 reset to `passes:false`.
- Scratchpad reset to logical iteration 1, status ACTIVE.
- **Next:** `./scripts/ralph/run_logical_iteration.sh 1` (R1 gate).
