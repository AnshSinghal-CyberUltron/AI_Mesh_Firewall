# MCP Frontend Adversarial — Run in Your Terminal

**Do NOT run the 20-iteration loop from a Cursor subagent** — long gates receive SIGTERM (~30–60s) when the agent session ends.

Run everything below in a **dedicated Terminal** (iTerm, Terminal.app, or `tmux`).

## One-time setup

```bash
cd /Users/anshsinghal/Desktop/AI_Security/AI_Mesh_Firewall

# Full reset (optional — kills batches, clears locks, resets R1–R16)
make mcp-adversarial-reset

# Docker stack
./scripts/ensure_mcp_sandbox_network.sh 2>/dev/null || true
docker compose --profile services up -d

# Verify broker + gateway
curl -sf http://127.0.0.1:8311/health | jq -e '.docker_ok==true'
curl -sf http://127.0.0.1:8300/health >/dev/null && echo gateway OK
curl -sf http://127.0.0.1:8180 >/dev/null && echo frontend OK
```

## Single iteration (foreground)

Logical iteration 1 = R1 (regression iter 5):

```bash
make mcp-adversarial-iter ITER=1
# or:
./scripts/ralph/run_logical_iteration.sh 1
```

Each logical iteration runs:

1. `node scripts/ralph/run_parallel_org_agents.mjs --full` (2 parallel orgs, 6 MCP servers each)
2. `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_sandbox_adversarial.py -q`
3. `BASE_URL=http://127.0.0.1:8180 node tests/e2e/mcp_sandbox_adversarial/frontend_parallel_orgs.mjs`
4. Iter 16 only: `cd frontend && npm run lint && npm run build`

Expect **~8–25 minutes per iteration** depending on Docker/pytest/Playwright load.

## Full 20 logical iterations (minimum campaign)

```bash
cd /Users/anshsinghal/Desktop/AI_Security/AI_Mesh_Firewall
for i in $(seq 1 20); do
  echo "========== LOGICAL ITERATION $i =========="
  ./scripts/ralph/run_logical_iteration.sh "$i" || exit 1
done
echo "All 20 logical iterations complete."
```

| Logical iter | Regression iter | PRD story | Notes |
|--------------|-----------------|-----------|-------|
| 1 | 5 | R1 | |
| … | … | … | |
| 16 | 20 | R16 | includes frontend lint+build |
| 17–20 | 21–24 | — | extra validation gates |

## Resume after failure

```bash
# Clear stale lock if no regression process running
pgrep -fl 'run_regression|run_logical' || rm -rf scripts/ralph/.regression.lockdir

# Resume from iteration N (e.g. 7)
for i in $(seq 7 20); do ./scripts/ralph/run_logical_iteration.sh "$i" || exit 1; done
```

## Logs

- Per-iteration detail: `scripts/ralph/regression.log`
- Scratchpad state: `.cursor/ralph/scratchpad.md`
- PRD pass flags: `scripts/ralph/prd-mcp-frontend-adversarial.json`

## Completion

When F0–F20 **and** R1–R16 all have `passes:true` and ≥20 logical iterations logged:

```
<promise>MCP FRONTEND ADVERSARIAL COMPLETE</promise>
```
