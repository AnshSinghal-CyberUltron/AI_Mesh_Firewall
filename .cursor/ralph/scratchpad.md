---
iteration: 3
max_iterations: 50
completion_promise: "COMPLETE and tested from frontend and backend"
---

# MCP Per-Tenant Docker Sandbox — Ralph Loop

**Source of truth:** `.skill-workspace/implementation_plan.md` (Phase 1 MVP — stdio transport, Docker runc, one sandbox per org/tenant).

## Architecture summary

- Extend `services/mcp-broker` as **Sandbox Controller** (not a new service).
- One long-lived Docker container per tenant (`org_slug`); sandbox-agent manages multiple stdio MCP processes inside.
- Gateway delegates spawn/stdio I/O via `mcp_sandbox_client.py` when `MCP_STDIO_IN_PROCESS=false` (prod default).
- Dev fallback: `MCP_STDIO_IN_PROCESS=true` keeps existing in-gateway `mcp_stdio_adapter.py` path.
- No npm package restrictions; full outbound network in sandboxes.
- Phase 1 runtime: Docker runc + cgroup limits; gVisor deferred to Phase 2.

## Ordered work breakdown

1. **Sandbox image** — `services/mcp-broker/sandbox-image/Dockerfile` (Node 20 + Python 3.12 + uv/npx); sandbox-agent FastAPI on port 9320.
2. **Broker controller** — Docker SDK lifecycle (`src/sandbox/docker_manager.py`), registry + reaper, REST routes (`/v1/sandbox/{org}/ensure`, `stdio/rpc`, `status`, `DELETE`), internal auth (`MCP_BROKER_INTERNAL_KEY`).
3. **Gateway client** — `gateway/ai_mesh_gateway/mcp_sandbox_client.py`; branch `mcp_stdio_adapter.send_jsonrpc` on `MCP_STDIO_IN_PROCESS`.
4. **Rate limits** — TPM + burst/RPM on `org_mcp_jsonrpc` in `mcp_proxy.py`.
5. **docker-compose** — Mount Docker socket on mcp-broker only; `mcp_sandbox_bridge` network; env vars for broker key and in-process flag.
6. **Tests** — Unit (agent, broker mock, gateway client); integration (`@pytest.mark.docker`); parallel load gate; frontend MCPConnectorPanel.

## Quality gates (ALL must pass before completion promise)

### Backend gate
```bash
cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests -q
```

### Broker gate
```bash
cd services/mcp-broker && pytest tests -q
```
(or `pytest services/mcp-broker/tests -q` from repo root once wired)

### Frontend gate
```bash
cd frontend && npm run lint && npm run build
```
Plus browser or Playwright verification: MCPConnectorPanel stdio registration works with sandbox path.

### Final acceptance gate — parallel load integration
Integration test `test_mcp_sandbox_parallel_load.py` (or equivalent) that:
- Spins up **5 org sandboxes concurrently** (`sandbox-org-1` … `sandbox-org-5`).
- Each org registers/runs **5 distinct stdio MCP servers** (mcp-stub variants or lightweight npx stubs).
- Concurrent `tools/list` + `tools/call` across all **25 server endpoints** without cross-tenant leakage.
- All pass with `MCP_STDIO_IN_PROCESS=false` and **real Docker**.

## Iteration rules

1. Read `scripts/ralph/prd-mcp-sandbox.json` — pick ONE highest-priority story with `passes:false` whose dependencies are satisfied.
2. Implement only that story; run its gate; commit on green; set `passes:true`.
3. Append learnings to `scripts/ralph/progress.txt` under `## MCP Sandbox Ralph Loop`.
4. Use gateway venv for gateway tests: `cd gateway && ./.venv/bin/python -m pytest ...`
5. Security: never inject gateway secrets into sandboxes; egress bytes remain source of truth for scan/audit (unchanged in `mcp_proxy.py`).

## Iteration 3 complete

- **S3-shared-stdio-common** — `shared/ai_mesh_shared/mcp_stdio_common.py`; denylist blocks `GATEWAY_INTERNAL_API_KEY`; gateway + sandbox-agent import shared module; commit `2217825f`.
- **Next:** S4-docker-manager (priority 2, unblocked) or S5/S6 depending on dependency order — S4 has no deps beyond S1 (done).

## Completion

Only output `<promise>COMPLETE and tested from frontend and backend</promise>` when **every** story in `prd-mcp-sandbox.json` has `passes:true` **and** all gates above are green with evidence.
