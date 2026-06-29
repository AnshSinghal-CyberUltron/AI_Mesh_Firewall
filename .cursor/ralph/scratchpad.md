---
iteration: 13
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

## Iteration 13 complete

- **T1-integration-docker** — `test_mcp_sandbox_integration.py` (3 tests, `@pytest.mark.docker`): broker in Docker on `mcp_sandbox_bridge`; labels `ai_mesh.role`/`ai_mesh.org_slug`; `broker_send_jsonrpc` + `send_jsonrpc` with `MCP_STDIO_IN_PROCESS=false`; cross-org volume + env isolation; gate 3 passed (~92s).
- **Next:** E1-parallel-load (5 orgs × 5 MCP servers concurrent).

## Iteration 12 complete

- **S12-rate-limit-mcp** — `org_mcp_jsonrpc` enforces TPM + burst/RPM via `_enforce_mcp_org_rate_limits`; 429 → JSON-RPC error envelope (HTTP 200); 4 tests in `test_mcp_rate_limit.py`; MCP gate 37 passed.
- **Next:** E1-parallel-load (`test_mcp_sandbox_parallel_load.py` 5×5 concurrent gate).

## Iteration 11 complete

- **S11-stdio-adapter-branch** — `send_jsonrpc` branches on `MCP_STDIO_IN_PROCESS` (default true); false → `broker_send_jsonrpc`; `_adapter_forward` passes `org_slug` in `server_config`; 4 tests in `test_mcp_stdio_adapter_branch.py`; MCP gate 37 passed.
- **Next:** S12-rate-limit-mcp (`org_mcp_jsonrpc` TPM + burst/RPM enforcement).

## Iteration 10 complete

- **S10-sandbox-client** — `gateway/ai_mesh_gateway/mcp_sandbox_client.py` (`ensure_sandbox`, `broker_send_jsonrpc`); `X-MCP-Broker-Key` from `MCP_BROKER_INTERNAL_KEY`; 503 exponential backoff; 502 → safe `RuntimeError`; timeouts from `MCP_STDIO_*`; 7 tests in `test_mcp_sandbox_client.py` (httpx mock); gate 7 passed.
- **Next:** S12-rate-limit-mcp (`org_mcp_jsonrpc` TPM + burst/RPM enforcement).

## Iteration 9 complete

- **S9-docker-compose** — `services/mcp-broker/Dockerfile` + `pyproject.toml` (docker SDK, uvicorn); compose adds `mcp_sandbox_bridge`, `mcp-sandbox-image` build, mcp-broker with docker.sock (broker only), `MCP_BROKER_INTERNAL_KEY` + `MCP_STDIO_IN_PROCESS=false` on gateway; `test_docker_compose.py` (2 tests); gate `docker compose --profile services config` exit 0; broker suite 59 passed.
- **Next:** S10-sandbox-client (gateway `mcp_sandbox_client.py` HTTP client).

## Iteration 8 complete

- **S8-broker-wire** — `main.py` GET /health returns `docker_ok` from `docker_manager.ping()`; sandbox router, registry, reaper lifespan already wired; `test_main_health.py` (3 tests); gate 57 passed.
- **Next:** S10-sandbox-client (gateway `mcp_sandbox_client.py`).

## Iteration 7 complete

- **S7-broker-auth** — `test_broker_auth.py` (9 tests): missing header → 401, wrong key → 401, unset/blank `MCP_BROKER_INTERNAL_KEY` fail-closed, valid key → 200 on protected route; `auth.py` already correct; gate 54 passed.
- **Next:** S8-broker-wire (health `docker_ok` + sandbox router wire in main).

## Iteration 6 complete

- **S6-broker-routes** — `routes.py` (POST ensure, POST stdio/rpc → agent /rpc, GET status, DELETE destroy) + `auth.py` (`X-MCP-Broker-Key` / `MCP_BROKER_INTERNAL_KEY`, fail closed); `touch_activity` on rpc; org quota `MCP_SANDBOX_MAX_ORGS`; 10 route tests (mock docker + httpx); gate 45 passed.
- **Next:** S7-broker-auth (dedicated auth test file) or S8-broker-wire (health docker_ok + full wire).

## Iteration 5 complete

- **S5-registry-reaper** — `registry.py` (thread-safe in-memory map: org_slug, container_id, agent_url, last_activity); `reaper.py` stops idle sandboxes after `MCP_SANDBOX_IDLE_TIMEOUT` (default 600s); `DockerManager` syncs registry; `main.py` lifespan starts/stops reaper; 6 tests with mock clock.
- **Next:** S6-broker-routes (depends on S4 + S2) or S7-broker-auth (depends on S6).

## Iteration 4 complete

- **S4-docker-manager** — `docker_manager.py` with ensure/start/stop/destroy; labels + `MCP_SANDBOX_*` resource limits; 11 mocked lifecycle tests; `main.py` wires `DockerManager` instance.
- **Next:** S5-registry-reaper (depends on S4) or S6-broker-routes (depends on S4 + S2).

## Completion

Only output `<promise>COMPLETE and tested from frontend and backend</promise>` when **every** story in `prd-mcp-sandbox.json` has `passes:true` **and** all gates above are green with evidence.
