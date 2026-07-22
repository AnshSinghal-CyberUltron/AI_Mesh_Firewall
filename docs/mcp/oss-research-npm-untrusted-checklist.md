# Untrusted-npm hardening checklist for the MCP sandbox (P2 item #11)

> Deliverable for `scripts/ralph/mcp_progress.md` **P2 item #11**: a *checkable* multi-tenant isolation
> hardening checklist specifically for **running untrusted npm/PyPI packages** inside the per-org sandbox.
> This is the npm/Node **supply-chain** layer that sits ON TOP OF the container-runtime layer from item #9
> (`oss-research-docker-hardening.md`, controls H1–H15). Both layers are required; this doc is the P7
> acceptance gate for items **#22–23**. Sources fetched via WebSearch (Snyk/pnpm/Supabase/lirantal/Splunk).

## The threat, precisely (why item #9 alone is not enough)
`npx <pkg>` / `uvx <pkg>` does **two** dangerous things by default:
1. **Fetches the latest version** of the package (and all transitive deps) from the public registry — an
   unpinned spec means any hijacked release is pulled the instant it's published (typosquat / account
   takeover / dependency-confusion).
2. **Runs npm lifecycle scripts** (`preinstall`/`install`/`postinstall`) with the sandbox user's full
   privileges — *before the MCP server even starts*. This is the exact vector of the **2025 Shai-Hulud**
   worm and the lifecycle-script RATs that harvest npm/GitHub/cloud creds: "any package in your dependency
   tree can execute arbitrary code … no sandbox, no approval prompt, no signature verification."

Distinct from that: the MCP server's **own runtime code is legitimately executed** (that's the point of
running it). So supply-chain controls **reduce what gets fetched/run at install time**, but they can never
make the server's runtime code safe — that is why the item-#9 container boundary (assume-RCE containment)
is the mandatory backstop. **Defense-in-depth = Layer A (this doc) ∧ Layer B (item #9).**

## Current repo state — the path asymmetry (verified)
| Control | Gateway in-process path (`mcp_stdio_adapter.py`) | Broker/agent path (`stdio_manager.py`) |
|---|---|---|
| Command allowlist | (npx/node/python/uvx) | ✅ `_ALLOWED_COMMANDS:37` |
| **Package allowlist** | ✅ `_PACKAGE_ALLOWLIST:75` (env `MCP_STDIO_PACKAGE_ALLOWLIST`), enforced `:362` — **default OFF (empty)** | ❌ **ABSENT** |
| **Require pinned versions** | ✅ `_REQUIRE_PINNED_PACKAGES:82` / `_is_pinned:154`, enforced `:367` — **default `false`** | ❌ **ABSENT** |
| **`--ignore-scripts`** | ❌ not set | ❌ not set |
| **Private registry** | ❌ not set | ❌ `docker_manager.py:263` sets only `NPM_CONFIG_CACHE`/`UV_CACHE_DIR` |

→ Two gaps: (1) **path parity** — the broker/agent path (the one that actually runs in the per-org Docker
sandbox) lacks the allowlist + pin controls the in-process path already has; (2) **install-time script
execution and registry** are unguarded on **both** paths.

## LAYER A — the checklist (each: control → exact repo change → acceptance test)

- [ ] **N1 — Disable install lifecycle scripts.** Set container env **`npm_config_ignore_scripts=true`**
  (npx/npm honor it) in `docker_manager.py:_run_kwargs` env block (alongside `:263`), and pass
  `--ignore-scripts` for any explicit `npm ci`. Kills `postinstall` RCE before server start.
  *Tradeoff:* packages needing a build-step postinstall (native modules) break → serve those **pre-baked**
  (N4) instead. *Accept:* a package with a malicious `postinstall` (test fixture that writes a canary file /
  attempts egress) is fetched but its script **does not run** (no canary, no egress in the #30 capture).

- [ ] **N2 — Require version-pinned specs.** Enable **`MCP_STDIO_REQUIRE_PINNED_PACKAGES=true`** on the
  gateway path AND add the same `_is_pinned` gate to the broker/agent path (`stdio_manager.py`, mirror
  `mcp_stdio_adapter.py:154/367`). Reject `pkg` / `pkg@latest`; require `pkg@1.2.3`.
  *Accept:* registering a server with an unpinned `npx <pkg>` arg is rejected with a clear policy error on
  **both** paths; `pkg@1.2.3` is accepted.

- [ ] **N3 — Package allowlist parity.** Populate **`MCP_STDIO_PACKAGE_ALLOWLIST`** (gateway path already
  enforces it at `:362`) AND port `_PACKAGE_ALLOWLIST` to the broker/agent path so the **sandbox** path is
  gated too (item #9 gap #23). Bare-name match after stripping scope/version (`_pkg_base_name:144`).
  *Accept:* `npx <not-allowlisted-pkg>` is rejected in the sandbox path, not just the in-process path.

- [ ] **N4 — Prefer pre-baked, vetted packages (offline-first).** Bake the allowlisted MCP server packages
  into the sandbox image at build time (vetted, with scripts, at pinned versions) so runtime `npx`/`uvx`
  hits the warm cache (`/var/npm-cache`, `/var/cache/uv`) and fetches/executes **nothing new**. Combined
  with N5 egress-deny, only pre-baked packages can run. *Accept:* with egress to the registry denied, an
  allowlisted pre-baked server still starts; a non-baked package fails closed (cannot fetch).

- [ ] **N5 — Registry pinning + egress deny (dependency-confusion defense).** Set
  **`NPM_CONFIG_REGISTRY`** / **`UV_INDEX_URL`**/`PIP_INDEX_URL` to a single trusted (ideally internal
  proxy) registry, and pair with item-#9 **H13 egress default-deny** so the sandbox can reach *only* that
  registry + each org's declared remote-MCP host — nothing else. Blocks dependency-confusion (internal
  name resolved from public registry) and exfil. *Accept:* #30 canary cannot be beaconed; a scoped-internal
  name is not silently pulled from the public registry.

- [ ] **N6 — uv/PyPI equivalents.** For `uvx`/`uv`: pin versions (N2), index-pin (N5), and prefer
  wheels-only where possible (sdists can run `setup.py` — the PyPI analogue of postinstall). *Accept:* an
  unpinned `uvx` spec is rejected; index is the trusted mirror only.

- [ ] **N7 — No secrets in the fetch environment.** Confirm the child env passed to `npx`/`uvx` carries no
  tokens a malicious install could exfil — already strong via `mcp_stdio_common._build_child_env` secret
  denylist + allowlist (item #2 §2, `:19/:81/:140`) and per-org `MCP_REMOTE_CONFIG_DIR` (`:144`).
  *Accept:* re-assert in #25 that no `GATEWAY_INTERNAL_API_KEY`/DB/AWS/registry-auth env reaches the child.

## LAYER B — container containment (the backstop; do NOT re-derive here)
Even with Layer A perfect, the server's runtime code runs — contain it with item #9:
`no-new-privileges` (H1), `cap_drop:ALL` (H2), seccomp/**gVisor** (H3/H10), `read_only` (H4), non-root uid
(H5), pids/mem/cpu/ulimits/disk (H6–H9), per-org network (H12), **egress deny-by-default** (H13, also N5),
`init=True` (H14). Full table + kwargs: `oss-research-docker-hardening.md`.

## Why not just switch package managers?
The research notes pnpm (`strictDepBuilds` default-blocks pre/postinstall since late-2025), Yarn Berry
(`enableScripts:false`), and Bun (blocks lifecycle scripts by default, `trustedDependencies` allowlist)
all block install scripts by default — a cleaner default than npm's blunt `--ignore-scripts`. But MCP
servers are distributed to run via `npx`/`uvx` (the ecosystem default), so switching the runner is high-risk
for compatibility. **Recommendation:** keep `npx`/`uvx` but apply N1 (`ignore_scripts`) to get the same
"no install scripts" property, plus N2–N5. Revisit pnpm/Bun only if a server genuinely requires a build step
that N4 pre-baking can't cover.

## P7 acceptance (items #22–23) — done when:
1. Broker/agent path has **parity** with the gateway path on package-allowlist + pin-requirement (N2, N3).
2. `npm_config_ignore_scripts=true` is set in the sandbox and a malicious-`postinstall` fixture is proven
   inert (N1) via the #30 egress capture.
3. Registry + egress are pinned deny-by-default (N5 + item-#9 H13) and the #30 canary cannot exfil.
4. Layer-B container controls (item #9 priority set) are applied and a runaway/hostile package is contained
   (no host access, no sibling-org access, resource-capped) — verified in #22.

## Sources
- [Snyk — npm security best practices after Shai-Hulud (2025)](https://snyk.io/articles/npm-security-best-practices-shai-hulud-attack/)
- [linuxsecurity.com — installing packages executes untrusted code](https://linuxsecurity.com/features/npm-install-security-risk)
- [pnpm — mitigating supply chain attacks](https://pnpm.io/supply-chain-security) (strictDepBuilds default-block)
- [Supabase — securing npm installs](https://supabase.com/docs/guides/security/npm-security) (`npm ci`, lockfile, containerized+network-isolated builds)
- [lirantal/npm-security-best-practices](https://github.com/lirantal/npm-security-best-practices) (`--ignore-scripts`, no per-package allowlist in npm CLI)
- [Splunk — npm supply chain attack detection](https://www.splunk.com/en_us/blog/security/npm-supply-chain-attack-detection-analysis.html)
- Companion repo docs: `oss-research-docker-hardening.md` (#9, Layer B), `broker-sandbox-lifecycle.md` (#2, env builder + path map).
