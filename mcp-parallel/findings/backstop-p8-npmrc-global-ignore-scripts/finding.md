# CHG-0142 — bake a global npmrc (ignore-scripts=true) into the sandbox image (supply-chain defense-in-depth)

**Change-id:** CHG-0142
**Date:** 2026-07-03
**Severity:** LOW-MEDIUM (defense-in-depth — closes item 8 sub-item (2). The runtime already pins
`npm_config_ignore_scripts=true` per spawned child (CHG-0044); this adds an image-level control so the
supply-chain RCE vector stays closed even for npx invocations that don't inherit that env.)
**Area:** HARDEN THE ARCHITECTURE — "no unknown npm on the host" / supply-chain (item 8 sub-item 2:
"bake a locked .npmrc into the sandbox image").
**Files:** `services/mcp-broker/sandbox-image/Dockerfile` (new global-npmrc RUN layer);
`services/mcp-broker/tests/test_sandbox_image.py` (+1 guard test). No application/runtime code change.
**Whose work it touches:** the shared sandbox image (architecture-hardening surface). Additive layer.

## Context — the existing control and its single point of reliance

Untrusted, tenant-registered stdio MCP servers are fetched with `npx`/`npm` inside the per-org sandbox.
An untrusted package's `preinstall`/`install`/`postinstall` lifecycle scripts are a classic supply-chain
RCE vector. CHG-0044 closed the runtime path: `_build_child_env` (`shared/ai_mesh_shared/mcp_stdio_common.py`)
force-pins `child["npm_config_ignore_scripts"] = "true"` unconditionally and LAST, so a malicious
server-spec `env` can't re-enable scripts, and it's set on the exact child that fetches the package.

That control is authoritative **but it is the only thing** disabling install scripts, and it lives in one
env-building function. It protects the one spawn path that calls `_build_child_env`. It does **not** cover:
- a manual/debug `npx` run by anyone who `exec`s into the sandbox,
- any future spawn path that forgets to route env through `_build_child_env`,
- a regression that drops or mistypes the env pin.

The sandbox image (`Dockerfile`) baked **no** npm-level hardening — no `.npmrc`, no global config — so
outside that one code path, `npx` defaulted to `ignore-scripts=false`.

## The fix (CHG-0142)

Bake a **global** npmrc into the image at npm's global-config path for the `/usr/local` prefix
(`/usr/local/etc/npmrc` = `$PREFIX/etc/npmrc`), created before `USER sandbox` so it is root-owned:

```dockerfile
RUN mkdir -p /usr/local/etc \
    && printf '%s\n' 'ignore-scripts=true' 'audit=false' 'fund=false' 'update-notifier=false' \
        > /usr/local/etc/npmrc
```

- `ignore-scripts=true` — install lifecycle scripts OFF for **every** npm/npx invocation in the sandbox,
  for **any** user, regardless of cwd or whether the env pin was set. The package's own bin still runs
  (that is the MCP server); only install-time scripts are suppressed.
- Root-owned, in `/usr/local/etc` — the unprivileged `sandbox` user cannot rewrite it to re-enable scripts.
- `audit=false` / `fund=false` / `update-notifier=false` — suppress npm's incidental network calls, which
  is aligned with the sandbox egress-lockdown (item 12) and speeds cold starts. Safe, non-behavioral.

### Precedence / why it can't be bypassed and can't break anything

npm config precedence: builtin < global (`/usr/local/etc/npmrc`) < user (`~/.npmrc`) < project (`./.npmrc`)
< env (`npm_config_*`) < CLI. So:
- The CHG-0044 runtime env pin (`npm_config_ignore_scripts=true`) still takes **precedence** and **agrees**
  → no behavior change for the primary path; this is strictly belt-and-suspenders.
- A malicious package cannot ship a project `.npmrc` that re-enables scripts for its own install, and even
  if a `.npmrc` appeared in the cwd, the env pin (higher precedence) would still win.
- Legitimate servers are unaffected: `ignore-scripts` never blocks running the installed tool, only its
  install hooks — and MCP servers documented for `npx` do not rely on postinstall to function.

## Verification

```
cd services/mcp-broker
./.venv/bin/python -m pytest tests/test_sandbox_image.py -q          # 5 passed
./.venv/bin/python -m pytest tests -q -k "not websocket"             # 167 passed, 0 failed
```

New guard `test_dockerfile_bakes_global_npmrc_ignore_scripts` asserts the Dockerfile REDIRECTS
`ignore-scripts=true` into `/usr/local/etc/npmrc` (collapses line continuations so the `printf ... >`
reads as one logical line) — locks the image-level control against a future Dockerfile regression. The
existing `@pytest.mark.docker` `test_sandbox_image_builds` (skipped without Docker) remains the real
build gate for a Docker-capable host.

## Scope / honesty note

Image/config defense-in-depth only; no runtime code change. I could not run `docker build` here (no Docker
in this env), so the gate is the source-level Dockerfile guard + the full broker suite — the actual image
build + a live malicious-postinstall egress-capture proof (item 8 sub-item 3) remain host-blocked and keep
item 8 at `[ ]`. Item 8 sub-item (1) (prod-enable `MCP_STDIO_REQUIRE_PINNED_PACKAGES=true`) and the private
registry pin also remain. Partial coverage is not completion.
