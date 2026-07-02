# mcp-broker — agent notes

The **Sandbox Controller**: gives each org ONE Docker container running an in-container
agent that executes stdio MCP servers. The gateway (`MCP_STDIO_IN_PROCESS=false`) delegates
here. Map + evidence: `docs/mcp/broker-sandbox-lifecycle.md`.

## Layout
- `src/sandbox/docker_manager.py` — container create/ensure, resource limits, per-org network+volume. **`_run_kwargs` (:247) is the single source of truth for all docker-run flags.**
- `src/sandbox/routes.py` — `/v1/sandbox/{org}/ensure|stdio/rpc|status`; quota (`_check_org_quota:66`), cold-start retry (`_post_agent_rpc:106`).
- `src/sandbox/registry.py` — **in-memory** org→container map (NOT persisted).
- `src/sandbox/reaper.py` — idle-container reaper (stops, does not destroy).
- `src/sandbox/docker_health.py` — TTL-cached docker ping gate.
- `sandbox-image/agent/{main,stdio_manager}.py` + `Dockerfile` — the PID-1 in-container agent.
- Shared env-sandboxing: `shared/ai_mesh_shared/mcp_stdio_common.py` (`_build_child_env`).

## Conventions / invariants (do NOT weaken)
- **One sandbox per org.** Isolation = per-org Docker network `mcp_sandbox_net_{org}` + per-org
  volume `mcp_sandbox_{org}_auth`. Never share a container/network/volume across orgs.
- **No unknown npm on the host.** Packages are fetched by `npx`/`uvx` **inside** the container
  only (`stdio_manager.py:256`, `shell=False`, argv exec form). Never shell out on the host.
- **Per-org credential isolation is enforced in `_build_child_env`** (`mcp_stdio_common.py:119`):
  secret-env denylist (`:19`), allowlist passthrough (`:81`), `LD_PRELOAD`/`DYLD_*` stripped (`:140`),
  per-org `MCP_REMOTE_CONFIG_DIR` pinned (`:144`). Keep this fail-closed.
- **Fail-closed broker auth**: every `/v1/sandbox/*` route requires `X-MCP-Broker-Key` (`auth.py:17`).
- Command allowlist `npx,node,python,python3,uvx,uv` (`stdio_manager.py:37/40`); path-qualified commands rejected (`:202`).

## Gotchas (verified — see request-path/lifecycle docs)
- **Readiness ≠ running.** `DockerManager.ensure` returns when the *container* is `running`
  (`container_status:132`), NOT when the in-container agent socket is bound or the MCP server is
  `initialize`-d (`_ensure_initialized:369`). `/health` (`agent/main.py:43`) only means uvicorn is
  bound. This is the B3 cold-start gap.
- **`EnsureRequest.warm` is currently ignored** (`routes.py:153`) — no eager readiness poll yet.
- **Registry is volatile.** On broker restart it is empty; running containers become orphans. The
  reaper only sweeps registry entries (`reaper.py:41`) — no label-based orphan reconciliation.
  Quota (`_check_org_quota`) counts the (post-restart empty) registry → bypassable + TOCTOU.
- **Reaper has no `try/except`** (`reaper.py:54`); a single Docker `APIError` kills reaping for the
  process lifetime. If you touch the reaper, wrap the loop body.
- **P7 container hardening is NOT set** in `_run_kwargs`: no `security_opt=no-new-privileges`, no
  `cap_drop=['ALL']`, no seccomp/apparmor, no `storage_opt`/volume-size cap, no `ulimits`, no
  run-enforced `user=`. `read_only`+tmpfs+mem/cpu/pids ARE set. gVisor only if `MCP_SANDBOX_RUNTIME` set.
- `/var/npm-cache` + `/var/cache` tmpfs are **exec-enabled** (needed for npx bin symlinks) — a
  deliberate hole in the `/tmp` noexec posture.
- Agent `/rpc` is **unauthenticated** — isolation relies entirely on the per-org network. The
  host-run broker branch (`docker_manager.py:280-282`) puts the container on the shared bridge,
  collapsing that isolation — avoid in multi-tenant deployments.
- `node -e` / `python -c` = arbitrary code; the command allowlist can't stop RCE via permitted
  interpreters — containment must come from the sandbox hardening, so the P7 gaps above matter.

## Tests
`cd services/mcp-broker && python -m pytest tests -q` — key suites: `test_sandbox_lifecycle.py`,
`test_sandbox_routes.py`, `test_sandbox_reaper.py`, `test_agent_ready_retry.py`, `test_broker_auth.py`,
`test_agent_rpc.py`, `test_sandbox_image.py`, `test_stdio_common.py`.
