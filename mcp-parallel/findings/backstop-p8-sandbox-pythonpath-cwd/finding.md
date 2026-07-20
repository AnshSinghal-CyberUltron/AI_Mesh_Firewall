# CHG-0153 — sandbox image put CWD on the agent's Python import path (PYTHONPATH trailing colon); + image-level verification of CHG-0142

**Change-id:** CHG-0153
**Date:** 2026-07-03
**Severity:** LOW (defense-in-depth — removes an implicit-CWD entry from a sandbox agent's import path, a
classic import-hijack footgun; also fixes the docker build `UndefinedVar` warning).
**Area:** HARDEN THE ARCHITECTURE — no untrusted-controllable import surface in the per-tenant sandbox
(item 8 / no-unknown-code-on-the-agent-path).
**Files:** `services/mcp-broker/sandbox-image/Dockerfile` (`ENV PYTHONPATH`).
**Whose work it touches:** the sandbox image (architecture-hardening surface).

## Root cause

`Dockerfile` had `ENV PYTHONPATH="/opt/shared:${PYTHONPATH}"`. `${PYTHONPATH}` is UNDEFINED at build time,
so the ENV resolved to the literal `"/opt/shared:"` — the **trailing colon** is an EMPTY `sys.path` entry,
which Python interprets as the process **current working directory**. Verified in the built image:
`PYTHONPATH=[/opt/shared:]` and `"" in sys.path == True`. So the sandbox agent (which spawns untrusted,
tenant-registered MCP servers) carried "whatever CWD happens to be" on its import path — a well-known
import-hijack footgun (a module planted in the CWD can shadow a real import). It was also the source of the
`UndefinedVar: '$PYTHONPATH' (line 45)` docker build warning.

## The fix (CHG-0153)

`ENV PYTHONPATH="/opt/shared"` — a plain assignment. Since `${PYTHONPATH}` is undefined at build time the
append was pointless, and `docker run -e PYTHONPATH=...` overrides the ENV entirely anyway, so nothing
legitimate is lost. This removes the empty (CWD) entry from the agent's `sys.path`.

Verified the agent still works (the empty entry was NOT load-bearing — `uvicorn agent.main:app` imports the
`agent` package itself, and `ai_mesh_shared` resolves via the explicit `/opt/shared`):

```
docker build -f services/mcp-broker/sandbox-image/Dockerfile -t ai-mesh/mcp-sandbox:chg0153-verify .
# -> no UndefinedVar warning
docker run -d -e ORG_SLUG=verify ai-mesh/mcp-sandbox:chg0153-verify
docker exec <cid> python -c "urllib.request.urlopen('http://127.0.0.1:9320/health')"
# -> {"status":"ok","service":"mcp-sandbox-agent",...}; logs: "Application startup complete"
# runtime PYTHONPATH=[/opt/shared] (no trailing colon)
```

## Bonus: image-level verification of CHG-0142 (npm supply-chain) + an operational finding

While rebuilding I verified CHG-0142 (the baked global npmrc) END-TO-END for the first time (previously only
Dockerfile-source-asserted, since the docker-build gate was thought host-blocked — docker IS available here):

- The rebuilt image bakes `/usr/local/etc/npmrc` = `ignore-scripts=true` / `audit=false` / `fund=false` /
  `update-notifier=false`; `npm config get ignore-scripts` → **true** (as the `sandbox` user);
  `npm config get globalconfig` → `/usr/local/etc/npmrc` (confirms CHG-0142 targeted the correct path); the
  file is `root:root -rw-r--r--` (the unprivileged user cannot rewrite it).

- **OPERATIONAL FINDING (action for the deploy owner):** the currently-BUILT `ai-mesh/mcp-sandbox:latest`
  image PREDATES CHG-0142 — it has NO `/usr/local/etc/npmrc` and `npm config get ignore-scripts` → **false**.
  So CHG-0142 (and CHG-0153) are in the Dockerfile but NOT in the running image; the sandbox image must be
  **rebuilt + redeployed** (and per-org sandboxes recreated) for these image-level controls to take effect.
  The per-spawn runtime env pin (`npm_config_ignore_scripts=true`, CHG-0044) is still active in the deployed
  image, so ignore-scripts is enforced at runtime today; the baked npmrc is the belt-and-suspenders layer
  awaiting a rebuild.

## Scope / honesty note

Small image hardening + fixes the build warning; the agent is verified still-functional. gVisor/runsc is
NOT installed on this host (`docker info` Runtimes: runc only), so item 12 remains genuinely blocked. Does
not change the host-blocked live-stress status. Partial coverage is not completion.
