# Docker sandbox hardening + multi-tenant patterns (P1 item #9)

> Deliverable for `scripts/ralph/mcp_progress.md` **P1 item #9**: study Docker sandboxing hardening
> (seccomp / gVisor / read-only rootfs / no-new-privileges / limits) + multi-tenant patterns for
> running **untrusted** npm/PyPI packages. This is the *external, sourced* companion to the repo's
> own PRESENT/ABSENT gap table in [`broker-sandbox-lifecycle.md`](./broker-sandbox-lifecycle.md) §2.
> It feeds **P7** (items #22–25) with the exact `docker-py` kwarg for each control.
>
> Threat premise (from [`ARCHITECTURE_AND_THREATS.md`](./ARCHITECTURE_AND_THREATS.md)): the code
> inside a sandbox is **attacker-controlled** — `npx <anything>` / `node -e` / `python -c` run
> arbitrary code the tenant chose. Containment cannot come from a command allowlist; it must come
> from the container runtime boundary. So the design goal is: **assume RCE inside the sandbox, and
> make that RCE worthless** — no host access, no sibling-org access, no unbounded resource use, no
> arbitrary egress.

## The control checklist (best-practice → exact kwarg → repo state → P7 action)

`docker_manager.py:_run_kwargs (:247)` is the single create-time source of truth; all "add" actions
below mean adding a key to that dict. CLI flags are mapped to their `docker-py` `containers.run/create`
kwarg because this repo drives Docker via the Python SDK, not the CLI.

| # | Control | CLI flag | `docker-py` kwarg | Repo state (evidence) | P7 action |
|---|---------|----------|-------------------|-----------------------|-----------|
| H1 | **No privilege escalation** | `--security-opt no-new-privileges` | `security_opt=['no-new-privileges:true']` | **ABSENT** (`docker_manager.py:247`) | ADD (#22) — cheap, no compat risk; blocks setuid escalation |
| H2 | **Drop all capabilities** | `--cap-drop ALL` | `cap_drop=['ALL']` (+ `cap_add=[]`) | **ABSENT** — default caps kept (NET_RAW/SETUID/CHOWN…) | ADD (#22) — sandbox needs *zero* caps (no bind <1024, no raw sockets) |
| H3 | **seccomp syscall filter** | `--security-opt seccomp=profile.json` | `security_opt=['seccomp=<json-or-path>']` | Docker **default** profile only (never overridden) | Keep default at minimum; ship a tightened profile OR prefer H10 (gVisor). Default already blocks ~44 syscalls incl. `keyctl`, `ptrace`(mostly), `mount`, `reboot` |
| H4 | **Read-only rootfs** | `--read-only` | `read_only=True` | **PRESENT** (`:272`) ✅ do NOT weaken | verify writes only to declared tmpfs/volume |
| H5 | **Non-root, run-pinned uid** | `-u 4000:4000` | `user='4000:4000'` | image `USER sandbox` (`Dockerfile:36`) but broker does **not** pin `user=` (`:261`) | ADD explicit `user=` at run (#25) — don't rely solely on image `USER` (a rebuilt/overridden image could ship root) |
| H6 | **PID limit (fork-bomb)** | `--pids-limit N` | `pids_limit=256` | **PRESENT** (`:271`) ✅ | keep; complements agent-side `MAX_PROCESSES` caps |
| H7 | **Memory / CPU caps** | `--memory`, `--cpus` | `mem_limit='2048m'`, `nano_cpus=1e9` | **PRESENT** (`:269`,`:270`) ✅ | add `--memory-swap == --memory` (`memswap_limit=mem_limit`) so swap can't defeat the RAM cap |
| H8 | **FD / nproc ulimits** | `--ulimit nofile=`, `--ulimit nproc=` | `ulimits=[docker.types.Ulimit(name='nofile',soft=1024,hard=2048), …]` | **ABSENT** (`:247`) | ADD (#22) — `pids_limit` bounds processes but not FDs; an FD leak still exhausts host handles |
| H9 | **Disk quota** | `--storage-opt size=10G` | `storage_opt={'size':'10G'}` | **ABSENT**; `/data/mcp-auth` volume grows unbounded (`:267`) | ADD **only if** backing FS is quota-capable (overlay2-on-xfs w/ `pquota`, btrfs, zfs, or devicemapper). On ext4/overlay2-without-pquota `storage_opt` is **rejected** → gate on runtime detection, else cap the *volume* via tmpfs-size or a periodic-usage reaper (#24) |
| H10 | **Kernel-isolation runtime** | `--runtime=runsc` (gVisor) / Kata | `runtime='runsc'` | Hook exists via `MCP_SANDBOX_RUNTIME` (`:283`) but default is **runc** (shared host kernel) | STRONGLY RECOMMEND runsc in prod (#22): gives VM-grade isolation for untrusted code without hand-writing a seccomp policy — "policies are extremely difficult (if not impossible) to reliably define" for arbitrary workloads (gVisor docs). Tradeoff: per-syscall overhead + some `/proc` & syscall gaps — validate the MCP servers still init under runsc |
| H11 | **No docker.sock mount** | never `-v /var/run/docker.sock` | (absence of `volumes` entry) | **PRESENT/OK** — broker mounts only the per-org auth volume (`:267`); sandbox has no socket | keep as an invariant; assert in a test that no sandbox mount is the docker socket |
| H12 | **Per-tenant network** | user-defined network, not default bridge | `network=mcp_sandbox_net_{org}` | **PRESENT** (`:225`,`:268`) ✅ — sibling orgs cannot reach each other's agent ports | keep; NB the **host-run broker branch** (`:280`) collapses this — treat host-run as dev-only |
| H13 | **Egress allowlist** | (no single flag — needs proxy/firewall) | `network=<no-nat-net>` + `HTTP(S)_PROXY` env → host allowlist proxy | **ABSENT** — per-org bridge has full NAT egress (needed for `npx` fetch) → a package can reach arbitrary hosts (`:268`) | ADD (#22/#23): default-deny egress; force all outbound through a host-side allowlist proxy (see pattern below). Allow only npm/PyPI registries + each org's declared remote-MCP hosts |
| H14 | **`--init` / tini PID-1** | `--init` | `init=True` | **ABSENT** — uvicorn is PID-1 (`Dockerfile:46`); npx→node grandchildren can zombie | ADD `init=True` (#24) so a reaping PID-1 harvests orphaned grandchildren |
| H15 | **User-namespace remap** | `--userns-remap=default` (daemon) | daemon-level (`/etc/docker/daemon.json`) | not configured | OPTIONAL defense-in-depth (#22): maps container-root→unprivileged host uid. Note: conflicts with some volume-uid setups — validate the auth-volume writes still work, or prefer H10 which subsumes this |

## Multi-tenant patterns for running UNTRUSTED code (the "shape", with sources)

1. **One sandbox per tenant, one user-defined network per tenant.** The repo already does this
   (`mcp_sandbox_net_{org}`, one container/org). This is the baseline that stops A↔B lateral movement.
   OWASP: create isolated Docker networks; avoid the default bridge.

2. **Assume-RCE containment stack** (defense-in-depth, apply *all*, not one): `read_only` + `cap_drop ALL`
   + `no-new-privileges` + non-root uid + resource caps + seccomp/gVisor. OWASP frames these as the
   layered set precisely because any single control is bypassable.

3. **Egress is the real exfil channel.** For untrusted workloads the recommended architecture is a
   **host-side HTTP/HTTPS proxy the sandbox is forced through, with default-deny**:
   - Docker's own AI-Sandbox design routes *all* sandbox traffic through a host proxy
     (`host.docker.internal:3128`) that enforces per-request allow rules and can MITM-terminate TLS to
     inspect/inject — presets are Open / Balanced(default-deny+common dev sites) / Locked-Down(deny-all).
   - The Claude Code dev-container ships a **default-deny iptables firewall** as the copy-and-adjust
     starting point.
   - `iron-proxy` is a purpose-built MITM egress firewall + DNS for untrusted workloads: default-deny
     at the network boundary, workload can reach only explicitly-allowed domains.
   - **Hardening note from the research:** allowlists must be paired with **canonicalization,
     IP-level deny rules, malformed-host rejection, and regression tests** — *exact host allowlists are
     safer than broad wildcards* (an attacker will register `npmjs.org.evil.com` against `*npmjs*`).
   - Repo application (#22/#23): the sandbox needs npm-registry + PyPI + each org's declared remote-MCP
     host (e.g. `mcp.linear.app` for the mcp-remote case, see
     [`oss-research-mcp-remote.md`](./oss-research-mcp-remote.md)) — everything else denied. The
     canary/leakage tests (#30) should confirm a planted secret cannot be beaconed out.

4. **Supply-chain: a *command* allowlist is not a *package* allowlist.** The broker/agent path allows
   only `npx,node,python,python3,uvx,uv` (`stdio_manager.py:37`) but any *package name* passed as an
   arg is fetched from npm/PyPI and executed inside the sandbox. The in-process gateway path already
   has `_PACKAGE_ALLOWLIST`/`_REQUIRE_PINNED_PACKAGES` (`mcp_stdio_adapter.py:362`) — the broker path
   does **not** (#23). Pairs with H13: even a malicious dependency is contained if egress is deny-by-default.

5. **Rootless / daemonless as the strongest daemon-level posture.** OWASP + Podman guidance: rootless
   mode means a container escape lands as an unprivileged host user, not root. Heavier lift than the
   run-time kwargs above; treat as a deployment-target recommendation, not a per-container change.

## What NOT to do (anti-patterns confirmed by the research)
- **Never** mount `/var/run/docker.sock` into a sandbox — equivalent to unrestricted host root (OWASP).
- **Don't** rely on the seccomp *default* alone for untrusted code if you can run gVisor — the default
  profile still permits a large syscall surface; gVisor removes the shared-kernel assumption entirely.
- **Don't** trust the image `USER` as the only non-root guarantee — pin `user=` at run (H5).
- **Don't** treat a broad wildcard egress allowlist as safe — canonicalize + exact-host match (§3).
- **Don't** leave `storage_opt` on for an incompatible FS — it hard-errors container create; detect first.

## Priority for P7 implementation (cheapest, highest-value first)
1. **H1 + H2 + H8 + H14** — pure `_run_kwargs` additions, near-zero compat risk, immediately shrink the
   escape/DoS surface. (item #22)
2. **H5** — pin `user=` at run. (item #25 cred/env isolation)
3. **H7 swap-cap + H9 disk quota (FS-gated)** — close the RAM-via-swap and disk-exhaustion holes. (#22/#24)
4. **H13 egress default-deny proxy** — biggest exfil-surface reduction; also needed for the #30 leakage
   proof. Ship the host allowlist proxy + `HTTP(S)_PROXY` env + strip NAT from the per-org net. (#22/#23)
5. **H10 gVisor (`runtime=runsc`)** — flip the default when the host supports it and MCP servers init
   cleanly under it; the single biggest isolation upgrade for untrusted code. (#22)

## gVisor (`runsc`) — host requirements + repo integration (P2.6)

gVisor is a user-space kernel (Sentry) that intercepts syscalls for OCI containers. It is the
recommended **kernel-isolation** upgrade for untrusted MCP code (H10) when seccomp policy authoring
for arbitrary npm/PyPI workloads is impractical.

### Host install + Docker registration

| Step | Action | Notes |
|------|--------|-------|
| 1 | Install `runsc` | `apt install runsc` (gvisor apt repo) or `curl -fsSL https://gvisor.dev/archive.key \| gpg --dearmor` + apt source. Binary must be world-readable/executable (`/usr/local/bin/runsc`). |
| 2 | Register runtime | `sudo runsc install` → adds `"runsc"` entry to `/etc/docker/daemon.json`; or manual `{ "runtimes": { "runsc": { "path": "/usr/bin/runsc" } } }`. |
| 3 | Restart Docker | `systemctl restart docker` (required after daemon.json change). |
| 4 | Smoke test | `docker run --rm --runtime=runsc hello-world` |
| 5 | Platform tuning | Bare metal: prefer **KVM** platform. VM nested: **systrap**. I/O-heavy (npx cache) and network-heavy workloads see overhead — acceptable for security-first MCP sandboxes. |

**Requirements:** Linux 4.14.77+, x86_64 or ARM64, Docker 17.09+. **Not available** on macOS Docker
Desktop (no KVM/runsc) — dev hosts stay on runc; prod Linux must enforce runsc.

### Repo hook today (`docker_manager.py`)

```text
SandboxDockerConfig.runtime  ← MCP_SANDBOX_RUNTIME env (:32)
_run_kwargs                  ← kwargs["runtime"] = config.runtime if set (:283-284)
```

- **Default:** `runtime` unset → Docker uses **runc** (shared host kernel).
- **Opt-in:** `MCP_SANDBOX_RUNTIME=runsc` passes `runtime='runsc'` to `containers.run`.
- **Test coverage:** `test_config_from_env` asserts runtime passthrough (`test_sandbox_lifecycle.py:219`).

### Fail-closed production pattern (P4 item #12 — design, not yet implemented)

Prod must **refuse to create sandboxes** if runsc is unavailable when hardening is required:

```text
MCP_SANDBOX_RUNTIME=runsc                    # desired runtime
MCP_SANDBOX_RUNTIME_REQUIRED=true            # NEW (proposed): fail closed in prod
```

**Proposed broker startup / create_container guard:**

1. If `MCP_SANDBOX_RUNTIME_REQUIRED=true` and `MCP_SANDBOX_RUNTIME` is unset → log fatal, refuse create.
2. Probe Docker: `docker info --format '{{json .Runtimes}}'` contains `runsc` (or `docker run --rm --runtime=runsc true`).
3. On probe failure → return 503 `sandbox_runtime_unavailable` (not silent runc fallback).
4. Dev override: `MCP_SANDBOX_RUNTIME_REQUIRED=false` (default) preserves macOS/local runc.

This mirrors OpenSandbox's pattern: *"Configured Docker runtime 'runsc' is not available → error"*.

**Compat validation before flip:** run `npx @modelcontextprotocol/server-everything stdio` initialize
handshake under runsc inside the sandbox image; watch for `/proc`, `epoll`, and network-stack gaps.

## Egress default-deny proxy architecture (H13 — P2.6 design)

Today `network=mcp_sandbox_net_{org}` is a bridge with **full NAT egress** (`docker_manager.py:268`).
That is required for `npx`/`uvx` registry fetch but allows arbitrary exfil (T3/T9, P9 #30).

### Target pattern (two layers — kernel + proxy)

```
┌─────────────────────────────────────────────────────────────┐
│ Host                                                         │
│  ┌──────────────────┐     ┌─────────────────────────────┐ │
│  │ allowlist proxy  │◄────│ nftables/iptables FORWARD     │ │
│  │ (Squid/Caddy/    │     │ DROP direct container→WAN     │ │
│  │  iron-proxy)     │     │ ALLOW only → proxy:3128       │ │
│  │ default-deny ACL │     └─────────────────────────────┘ │
│  └────────▲─────────┘                                        │
│           │ HTTP(S)_PROXY=http://host.docker.internal:3128   │
│  ┌────────┴─────────┐                                        │
│  │ mcp_sandbox_net_ │  per-org bridge (broker attached)     │
│  │ {org}            │                                        │
│  │  ┌─────────────┐ │                                        │
│  │  │ sandbox ctr │ │  env: HTTP_PROXY, HTTPS_PROXY,         │
│  │  │ (runsc)     │ │       NO_PROXY=broker,agent,metadata  │
│  │  └─────────────┘ │                                        │
│  └──────────────────┘                                        │
└─────────────────────────────────────────────────────────────┘
```

### Allowlist contents (per org, dynamic)

| Destination class | Examples | Why |
|-------------------|----------|-----|
| npm registry | `registry.npmjs.org` | `npx` fetch |
| PyPI | `pypi.org`, `files.pythonhosted.org` | `uvx` fetch |
| Declared remote MCP hosts | `mcp.linear.app`, per-server URL host from control DB | mcp-remote / future in-sandbox HTTP proxy |
| **Deny all else** | `*.evil.com`, IP literals, metadata `169.254.169.254` | exfil / SSRF |

**Hardening rules (from iron-proxy / Doable / Claude sandbox research):**

- **Exact-host match** after canonicalization — reject `registry.npmjs.org.attacker.com`.
- **IP-level deny** for non-proxy egress (nftables) so CONNECT bypass cannot skip the proxy.
- **Malformed Host header** rejection at proxy.
- **#30 canary test:** plant secret in Org B; prove Org A egress capture never contains it.

### `_run_kwargs` changes for H13 (P7 #22)

Add to container `environment` when proxy is deployed:

```python
"HTTP_PROXY": os.environ.get("MCP_SANDBOX_HTTP_PROXY", "http://host.docker.internal:3128"),
"HTTPS_PROXY": os.environ.get("MCP_SANDBOX_HTTPS_PROXY", "http://host.docker.internal:3128"),
"NO_PROXY": "127.0.0.1,localhost,172.16.0.0/12",  # broker agent on org net
```

Pair with host-side proxy compose service + iptables rules (outside `docker_manager.py` scope).

## `_run_kwargs` field-by-field map (verified 2026-07-02)

Anchor: `docker_manager.py:_run_kwargs` lines 247–285.

| `docker-py` kwarg | Present? | Value / condition | P7 |
|-------------------|----------|-------------------|-----|
| `image` | ✅ | `config.image` | — |
| `name` | ✅ | `container_name(org)` | — |
| `detach` | ✅ | `True` | — |
| `labels` | ✅ | role + org_slug | — |
| `environment` | ✅ | ORG_SLUG, MCP_REMOTE_CONFIG_DIR, npm/uv cache paths | H13 adds PROXY vars |
| `volumes` | ✅ | per-org auth volume → `/data/mcp-auth` rw | H9 size cap |
| `network` | ✅ | per-org net; shared bridge if host-run broker | H12 ✅ |
| `mem_limit` | ✅ | `2048m` default | H7 ✅; add memswap_limit |
| `nano_cpus` | ✅ | 1.0 CPU default | H7 ✅ |
| `pids_limit` | ✅ | 256 | H6 ✅ |
| `read_only` | ✅ | `True` | H4 ✅ |
| `tmpfs` | ✅ | `/tmp` noexec; `/var/npm-cache` exec; `/var/cache` | H4 ✅ |
| `ports` | conditional | host-run broker only (`:280-282`) | dev-only branch |
| `runtime` | conditional | only if `MCP_SANDBOX_RUNTIME` set | H10 — require in prod |
| `security_opt` | ❌ | — | H1 ADD |
| `cap_drop` | ❌ | — | H2 ADD |
| `cap_add` | ❌ | — | H2 (keep empty) |
| `user` | ❌ | image USER sandbox only | H5 ADD `4000:4000` |
| `ulimits` | ❌ | — | H8 ADD |
| `init` | ❌ | — | H14 ADD |
| `memswap_limit` | ❌ | — | H7 ADD (= mem_limit) |
| `storage_opt` | ❌ | — | H9 ADD (FS-gated) |

## Per-tenant patterns (summary)

| Pattern | Repo state | Source |
|---------|------------|--------|
| One container per org | ✅ `ensure(org_slug)` | OWASP custom networks |
| One network per org | ✅ `mcp_sandbox_net_{org}` | blocks sibling agent RPC |
| One auth volume per org | ✅ `mcp_sandbox_{org}_auth` | OAuth token isolation |
| Assume-RCE stack | partial (read_only + limits only) | OWASP layered controls |
| Egress default-deny | ❌ full NAT | Docker AI Sandbox / iron-proxy |
| gVisor kernel isolation | opt-in env only | gVisor production guide |

## Sources
- [OWASP Docker Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Docker_Security_Cheat_Sheet.html) — core control list (no-new-privileges, cap-drop, read-only, non-root, seccomp/AppArmor, resource limits, no docker.sock, custom networks, rootless).
- [gVisor docs](https://gvisor.dev/docs/) — runsc runtime, `--runtime=runsc` + daemon.json, VM-grade isolation vs. seccomp policy-authoring difficulty, perf/compat tradeoffs.
- [gVisor Docker quick start](https://gvisor.dev/docs/user_guide/quick_start/docker/) — `runsc install`, daemon restart, smoke test.
- [gVisor production guide](https://gvisor.dev/docs/user_guide/production/) — when to sandbox, KVM vs systrap, I/O/network overhead.
- [google/gvisor](https://github.com/google/gvisor) — runsc OCI runtime source.
- [Docker resource constraints](https://docs.docker.com/engine/containers/resource_constraints/) — `--memory`/`--memory-swap`/`--cpus`/`--cpuset` syntax.
- [Docker AI Sandbox network policies](https://docs.docker.com/ai/sandboxes/network-policies/) — default-deny host proxy (`host.docker.internal:3128`), Open/Balanced/Locked-Down presets, MITM TLS.
- [Claude Code sandbox environments](https://code.claude.com/docs/en/sandbox-environments) — default-deny iptables firewall dev-container reference.
- [iron-proxy](https://github.com/ironsh/iron-proxy) — MITM egress firewall + DNS for untrusted workloads, default-deny domain allowlist.
