# Broker Sandbox Lifecycle — file:line map + resource limits

> Deliverable for `scripts/ralph/mcp_progress.md` **P0 item #2**: map the broker
> sandbox lifecycle `create → install → start stdio → health → reap` + resource limits
> with `file:line` anchors. All anchors read from the tree and spot-verified (commit `4d970d1b`).
>
> Two processes are involved:
> 1. **Broker** (`services/mcp-broker/src/`) — orchestrates Docker containers, one per org.
> 2. **In-container agent** (`services/mcp-broker/sandbox-image/agent/`) — PID-1 FastAPI
>    service inside each sandbox that lazily spawns the stdio MCP child.
>
> Feeds item #5 (ARCHITECTURE_AND_THREATS) and the fix items: **B3** (#19-21),
> **P7 hardening** (#22-23), **reaper/restart-safety** (#24), **cred/env isolation** (#25).

## Lifecycle at a glance

```
register/first-RPC → broker routes.stdio_rpc(routes.py:161)
  ├─ docker gate: cached_docker_ok (docker_health.py:22)  → 503 if daemon not warm
  ├─ _check_org_quota (routes.py:66, MAX_ORGS=50)          → 429 if over cap
  ├─ _resolve_running_sandbox (routes.py:93) → DockerManager.ensure (docker_manager.py:345)
  │     ├─ CREATE:  ensure_org_network(:225) → create_container(:300, _run_kwargs:247)
  │     │           returns as soon as container State=='running' (container_status:132)  ← B3 readiness gap
  │     └─ _sync_registry(:175) → SandboxRegistry.register (registry.py:27)
  ├─ _post_agent_rpc (routes.py:106) POST {agent_url}/rpc, 8-retry cold-start backoff
  │     └─ IN-CONTAINER agent /rpc (agent/main.py:54)
  │           └─ send_jsonrpc(stdio_manager.py:411) → _ensure_process(:194)   ← INSTALL+START
  │                 ├─ command allowlist(:202/:37) → create_subprocess_exec(:256, shell=False)
  │                 │     (npx/uvx LAZILY fetch pkg from npm/PyPI INSIDE container)
  │                 └─ _ensure_initialized(:369) MCP initialize handshake         ← real HEALTH
  └─ reap: broker reaper.py (idle containers) + agent stdio_manager _reaper_loop:486 (idle children)
```

## 1. CREATE + resource limits — `docker_manager.py`

`SandboxDockerConfig.from_env` (`:30`) → `_run_kwargs` (`:247`) is the **single source of
truth** for container create-time flags:

| Limit | Value (default / env) | file:line |
|---|---|---|
| Memory (`mem_limit`) | `2048m` / `MCP_SANDBOX_MEMORY_MB` | `docker_manager.py:269` |
| CPU (`nano_cpus`) | `1.0` CPU / `MCP_SANDBOX_CPUS` | `:270` |
| PIDs (`pids_limit`) | `256` / `MCP_SANDBOX_PIDS_LIMIT` | `:271` |
| tmpfs `/tmp` | `rw,noexec,nosuid,size=512m` | `:274` |
| tmpfs `/var/npm-cache` | `rw,exec,nosuid,size=1g,mode=1777` | `:275` |
| tmpfs `/var/cache` | `rw,exec,nosuid,size=512m,mode=1777` | `:276` |
| Agent RPC timeout | `130s` / `MCP_BROKER_AGENT_TIMEOUT` | `routes.py:19` |
| Cold-start agent retries | `8`, base 0.4s / max 2.0s | `routes.py:26` |
| Per-org quota (orgs) | `50` / `MCP_SANDBOX_MAX_ORGS` | `routes.py:58` |
| Idle reap timeout | `600s` / `MCP_SANDBOX_IDLE_TIMEOUT` | `reaper.py:26` |
| Reaper interval | `60s` / `MCP_SANDBOX_REAPER_INTERVAL` | `reaper.py:27` |
| docker_ok cache TTL | `15s` | `docker_health.py:13` |
| Stop grace | `30s` | `docker_manager.py:386` |

`create_container` (`:300`): 3-attempt 409-name-conflict recovery (reuse-if-running / force-remove+backoff).
`ensure` (`:345`): ensure org network → `find_container:105` → create-or-`_start_or_recreate` → reload → `_sync_registry:175`.

## 2. Isolation controls — PRESENT vs. ABSENT

**Present (do NOT weaken):**
- Per-org Docker network `mcp_sandbox_net_{org}` (`docker_manager.py:225`, comment `:95` "cannot reach sibling agent ports"); container joins via `network=org_net` (`:268`).
- Per-org named volume `mcp_sandbox_{org}_auth` bound `/data/mcp-auth` rw (`:267`).
- Read-only rootfs `read_only=True` (`:272`); tmpfs `/tmp` `noexec,nosuid` (`:274`).
- Non-root `USER sandbox` in image (`Dockerfile:36-39`, nologin shell).
- **Per-org credential/env isolation (item #25 — already strong):** `shared/ai_mesh_shared/mcp_stdio_common.py` `_build_child_env:119` — secret denylist strips `GATEWAY_INTERNAL_API_KEY`/`MCP_BROKER_INTERNAL_KEY`/DB/REDIS/AWS/`SECRET_KEY`/`PYTHONPATH` (`_SECRET_ENV_DENYLIST:19`); env passthrough is an **allowlist** (`:81`); `LD_PRELOAD`/`DYLD_INSERT_LIBRARIES` stripped (`:140`); per-org `MCP_REMOTE_CONFIG_DIR` pinned (`:144`) so OAuth tokens can't cross orgs.
- No-shell spawn `create_subprocess_exec` (argv, not shell) (`stdio_manager.py:256`); command path-block (`:202`) + command allowlist `npx,node,python,python3,uvx,uv` (`:37/:40`).
- Two-level caps: broker `pids_limit=256`; agent global `MCP_STDIO_MAX_PROCESSES=20` (`stdio_manager.py:29`) + per-org `MCP_STDIO_MAX_PROCESSES_PER_ORG=16` (`:34`) via LRU eviction; init semaphore `MCP_STDIO_MAX_CONCURRENT_INITS=4` (`:35`).

**ABSENT — P7 hardening gaps (items #22-23), confirmed at BOTH broker and image layers:**
| Gap | Where | Note |
|---|---|---|
| `security_opt=no-new-privileges` | `docker_manager.py:247` | setuid escalation not blocked |
| `cap_drop=['ALL']` | `docker_manager.py:247` | keeps default caps (NET_RAW, SETUID, CHOWN…) |
| custom seccomp / AppArmor | `docker_manager.py:247` | Docker defaults only |
| `storage_opt` / volume size cap | `docker_manager.py:267` | `/data/mcp-auth` volume grows unbounded → host-disk exhaustion |
| `ulimits` (nofile/nproc) | `docker_manager.py:247` | only `pids_limit` restrains fork-bombs; no FD cap |
| run-enforced `user=` | `docker_manager.py:261` | relies on image `USER`; broker doesn't pin uid |
| gVisor/Kata runtime | `docker_manager.py:283` | only if `MCP_SANDBOX_RUNTIME` set; default runc (shared kernel) |
| egress allowlist/firewall | `docker_manager.py:268` | per-org bridge has NAT egress (needed for npx fetch) → package can reach arbitrary hosts |
| agent `/rpc` auth | `agent/main.py:54` | unauthenticated; relies solely on per-org network |
| npm/PyPI **package** allowlist | `stdio_manager.py:40` | only a *command* allowlist; any package name in args is fetched+run (supply-chain surface). NB: the *in-process* gateway path has `_PACKAGE_ALLOWLIST`/`_REQUIRE_PINNED_PACKAGES` (`mcp_stdio_adapter.py:362-371`) — the broker/agent path does **not**. |
| tini/`--init` PID-1 | `Dockerfile:46` | uvicorn is PID-1; grandchildren (npx→node→server) can orphan/zombie |
| per-child rlimits | `stdio_manager.py:256` | no `preexec_fn`/RLIMIT on spawned child |

⚠️ Edge cases to close in P7: (a) **`node -e` / `python -c` = arbitrary code** — the command
allowlist can't prevent RCE via the permitted interpreters (inherent; containment must come
from the sandbox hardening above). (b) **org_slug sanitization divergence** — container/network
name uses `re.sub([^a-zA-Z0-9_.-]→'-')` (`docker_manager.py:87`) while volume uses `'_'`; distinct
raw slugs could collide to one sandbox if not normalized upstream (verify in #25). (c) **host-run
broker branch** (`:280-282`) puts the container on the shared bridge, collapsing per-org network
isolation in that deployment mode.

## 3. INSTALL + START + HEALTH (in-container agent)

- **INSTALL** = lazy `npx <pkg>` / `uvx <pkg>` fetch from npm/PyPI at spawn time, **inside** the
  container (`stdio_manager.py:256`) — never on the host (satisfies "no unknown npm on host"). npm
  cache lands on the exec-enabled tmpfs `/var/npm-cache` because the **broker's `_run_kwargs`
  injects** `NPM_CONFIG_CACHE`/`UV_CACHE_DIR`/`XDG_CACHE_HOME` as container env
  (`docker_manager.py:262-266`) — the image itself does **not** set these.
- **START** = `_ensure_process:194` (validate command → reuse-if-env-matches → cap-evict → spawn).
- **HEALTH (two distinct signals):**
  - `GET /health` (`agent/main.py:43`) + Dockerfile `HEALTHCHECK` (`Dockerfile:43`) report only that
    **uvicorn is bound** (+ `process_count`) — NOT that any MCP child is spawned/initialized.
  - Real readiness = `proc.initialized` set by `_ensure_initialized:369` (MCP `initialize` +
    `notifications/initialized`) on the **first** `/rpc`.

## 4. REAP + restart-safety (item #24)

- **Broker reaper** (`reaper.py`): `reap_idle_sandboxes:31` stops (`docker_manager.stop`, not
  `destroy`) + unregisters entries idle > 600s. Started once at boot (`main.py:40`).
- **Agent reaper** (`stdio_manager.py:486`): kills idle (>600s) / hung-uninitialized (>180s) children.

**Restart-safety findings (gaps to fix in #24):**
1. **Registry is in-memory only** (`registry.py:23`, instantiated empty `main.py:14`) — a broker
   restart drops all org→container mappings while containers keep running.
2. **No boot-time label reconciliation** — `main.py` lifespan warms docker + starts reaper but
   never enumerates `ai_mesh.role=mcp-sandbox` labeled containers into the registry. Re-adoption is
   lazy, only on the next `ensure`/RPC (`_sync_registry:175` via `find_container:105`).
3. **Orphan leak** — the reaper iterates *only* `registry.idle_entries` (`reaper.py:41`); a
   restart-orphaned container whose org never calls again is **never reaped** (CPU/RAM/disk +
   unbounded volume leak). No label-based orphan sweep exists.
4. **Reaper has no `try/except`** (`reaper.py:54`) and is never re-armed → one transient Docker
   `APIError` from `container.stop()` kills the reap task **permanently** for the process lifetime.
   (The agent-side reaper IS idempotently re-armed at `stdio_manager.py:504` — asymmetry.)
5. **Quota bypass** — `_check_org_quota` counts `len(registry.list())` (`routes.py:74`): after a
   restart the empty registry resets the 50-cap to 0 despite live containers; and check-then-ensure
   is a **TOCTOU** race (sync def in threadpool, `routes.py:66`+`:155` not atomic).
6. Reaper uses `stop` not `destroy` → stopped containers + volumes accumulate (`docker_manager.destroy:392` only via `DELETE /{org}`).

## 5. B3 (cold-start) relevance

- **Readiness = container `running`, not agent-socket-bound.** `ensure` returns the instant
  `container_status:132` sees `State=='running'` (PID-1 up); it never polls the agent `/health`.
  The `EnsureRequest.warm` flag defaults `True` but `ensure_sandbox` **ignores it** (`routes.py:153`).
  The only readiness mitigation is reactive: `_post_agent_rpc` 8-retry backoff (`routes.py:106`).
- **`agent_url` can resolve to `127.0.0.1`** right after create (org-net IP not yet assigned,
  `docker_manager.py:140`) — a first-request mis-routing hazard.
- **Docker warm-up window** — `_docker_ok_cache` starts `False` (`docker_health.py:11`); until the
  first ping lands (`_warm_docker_ok` up to ~24s, `main.py:27`), ensure/RPC return 503 "Docker
  unavailable" → client maps to "starting; retry" (distinct from the B3 502 string).
- **Fix direction (#19-21):** eager-provision on register/authorize/first-sync (call `ensure` +
  poll `/health` until `proc`-capable), have the broker return a clear **provisioning** state (503
  "starting", already retried) instead of collapsing agent errors to 502, and add a readiness poll +
  bounded backoff in `mcp_sandbox_client` that treats provisioning distinctly from hard failure.

## Verification
Spot-verified (all matched exactly): `docker_manager.py` 30,87,105,132,140,175,247,269-276,300,345,386,392;
`routes.py` 19,58,66,74,93,106,153,155,168,207; `docker_health.py` 11,13,22; `main.py` 14,27,40;
`registry.py` (full read) 23,27,41,68; `reaper.py` (full read) 26,27,31,41,54,59; `stdio_manager.py`
37,40,194,202,256,369,411,486,504; `agent/main.py` 43,54; `Dockerfile` 36,37,39,43,46;
`mcp_stdio_common.py` 19,119,140,144.
