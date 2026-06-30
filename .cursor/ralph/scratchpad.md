---
iteration: 9
min_iterations: 20
max_iterations: 50
completion_promise: "MCP FRONTEND ADVERSARIAL COMPLETE"
status: ACTIVE
active_story: R9-regression-iter13
prd: scripts/ralph/prd-mcp-frontend-adversarial.json
note: Cursor multitask Ralph — MCP frontend adversarial regression (BLOCKED Docker exhaustion)
---

# MCP Frontend Adversarial — Cursor Ralph Loop

You are one **Cursor Agent** iteration of the Ralph loop for story **R9-regression-iter13**.

## DO NOT USE

- `claude` CLI or `claude --print`
- `scripts/ralph/ralph.sh`, `ralph-mcp-frontend-adversarial.sh`, or any bash loop that spawns Claude Code headless

## Iteration playbook

Follow **exactly** `scripts/ralph/CLAUDE-mcp-frontend-adversarial.md`.

## Active story: R9-regression-iter13

### Full gate order

1. `node scripts/ralph/run_parallel_org_agents.mjs --full`
2. `cd gateway && MCP_ADVERSARIAL_USE_LIVE_BROKER=true MCP_ADVERSARIAL_RESET_SANDBOXES=true MCP_BROKER_URL=http://127.0.0.1:8311 MCP_BROKER_INTERNAL_KEY=dev-mcp-broker-key-change-me ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_sandbox_adversarial.py -q`
3. `BASE_URL=http://127.0.0.1:8180 node tests/e2e/mcp_sandbox_adversarial/frontend_parallel_orgs.mjs`

### BLOCKER (2026-06-30)

Docker daemon wedged after 8 consecutive gate cycles. Control :8100 health hangs; `docker exec` into sandboxes hangs; sandbox-agent never becomes healthy. **Recovery:** restart Docker Desktop, then:

```bash
docker rm -f $(docker ps -aq --filter name=adv-org) $(docker ps -aq --filter name=mcp-broker-adversarial) $(docker ps -aq --filter name=mcp-broker-integration) $(docker ps -aq --filter name=mcp-broker-parallel) 2>/dev/null || true
./scripts/ensure_mcp_sandbox_network.sh
docker compose --profile services up -d mcp-broker gateway control frontend
# Rebuild sandbox image after stdio lock fix:
docker build -t ai-mesh/mcp-sandbox:latest -f services/mcp-broker/sandbox-image/Dockerfile .
```

---

## Iteration log

- 2026-06-30 iter 1–8: R1–R8 regression gates green (cursor_ralph_iter1–8.log).
- 2026-06-30 iter 9: Gate 1 fail — cross_org_leak_probe round 8 tools/list missing echo; retry fail sandbox-agent unhealthy 120s. Docker exhaustion. R9 passes:false.
