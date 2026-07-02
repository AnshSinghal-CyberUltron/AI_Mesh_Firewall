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

## Sources
- [OWASP Docker Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Docker_Security_Cheat_Sheet.html) — core control list (no-new-privileges, cap-drop, read-only, non-root, seccomp/AppArmor, resource limits, no docker.sock, custom networks, rootless).
- [gVisor docs](https://gvisor.dev/docs/) — runsc runtime, `--runtime=runsc` + daemon.json, VM-grade isolation vs. seccomp policy-authoring difficulty, perf/compat tradeoffs.
- [Docker resource constraints](https://docs.docker.com/engine/containers/resource_constraints/) — `--memory`/`--memory-swap`/`--cpus`/`--cpuset` syntax.
- [Docker AI Sandbox network policies](https://docs.docker.com/ai/sandboxes/network-policies/) — default-deny host proxy (`host.docker.internal:3128`), Open/Balanced/Locked-Down presets, MITM TLS.
- [Claude Code sandbox environments](https://code.claude.com/docs/en/sandbox-environments) — default-deny iptables firewall dev-container reference.
- [iron-proxy](https://github.com/ironsh/iron-proxy) — MITM egress firewall + DNS for untrusted workloads, default-deny domain allowlist.
