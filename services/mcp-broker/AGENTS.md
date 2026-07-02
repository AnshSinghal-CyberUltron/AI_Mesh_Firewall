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

## Sandbox memory / graceful OOM (CP20)
- Per-org sandbox `mem_limit == memswap_limit` (swap disabled). Without a Node heap
  cap, a heavy Node MCP server (Ruflo-class) that exceeds `MCP_SANDBOX_MEMORY_MB`
  (default 2048) is kernel-SIGKILLed (exit -9 / 137) with no graceful signal.
- `docker_manager._run_kwargs` sets `NODE_OPTIONS=--max-old-space-size=<node_heap_mb>`
  in the sandbox env — default `max(256, memory_mb*0.75)` (1536 for 2048), overridable
  via `MCP_SANDBOX_NODE_MAX_OLD_SPACE_MB` / `SandboxDockerConfig.node_max_old_space_mb`;
  any operator `MCP_SANDBOX_NODE_OPTIONS` is preserved (appended). This makes a hungry
  Node server hit a graceful V8 "JavaScript heap out of memory" abort BELOW the cgroup
  cap (headroom for the Python agent + co-tenant servers) instead of a silent SIGKILL.
  To run genuinely heavy servers, raise `MCP_SANDBOX_MEMORY_MB` (compose broker env).
- `stdio_manager._classify_exit_reason(rc, stderr_tail, *, oversized_line)` is the pure,
  unit-tested exit-reason builder. It categorizes OOM FIRST from the stderr signature
  ("heap out of memory" / "reached heap limit") OR a kernel OOM code (-9/137) so the text
  contains "ran out of memory" → the control-plane classifier maps it to
  `MCP_OUT_OF_MEMORY` (a bare 134/SIGABRT would otherwise read as a generic crash). The
  gateway `mcp_stdio_adapter` mirrors this. Tests: `tests/test_stdio_exit_reason.py`,
  `tests/test_sandbox_lifecycle.py::test_run_kwargs_set_node_heap_cap_*`.

## Heavy servers, npm-cache storage + Ruflo (CP21)
- `/var/npm-cache` is a RAM-backed tmpfs (counts against the mem cgroup), size =
  `SandboxDockerConfig.npm_cache_size_mb` (env `MCP_SANDBOX_NPM_CACHE_SIZE_MB`, default
  1024). A heavy server with a large dependency tree (Ruflo) overflows the default and
  fails with ENOSPC → `_classify_exit_reason` (and the control classifier) map "no space
  left"/"enospc" to a clean `MCP_INSUFFICIENT_STORAGE` message telling the operator to
  raise the knob. Because tmpfs eats the cgroup, at default 2GB mem the npm-cache + node
  RSS hit the memory limit → **OOM (-9) is usually the binding constraint before the disk
  fills** (→ `MCP_OUT_OF_MEMORY`).
- **Ruflo is a genuinely heavy server** (koa/native addons/ONNX embedder; needs
  huggingface.co egress). It CONNECTS and lists real tools (agent_spawn / swarm_init /
  memory_store / hooks_*) at ~`MCP_SANDBOX_MEMORY_MB=4096` + `MCP_SANDBOX_NPM_CACHE_SIZE_MB=3072`
  (proven via `docker run` probe of the sandbox image). Default multi-tenant limits stay
  modest → Ruflo clean-errors (MCP_OUT_OF_MEMORY) with actionable dev guidance rather than
  a raw crash. Verify: `scripts/ralph/mcp_page_cp21_ruflo.py` (clean-error branch, product path).
