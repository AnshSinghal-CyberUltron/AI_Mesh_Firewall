# BACKSTOP finding — npm install lifecycle scripts execute for the spawned stdio child (CHG-0044)

- **Item:** G3 item 8 ("No unknown npm on host — proven") — supply-chain hardening.
- **Change-id:** CHG-0044 (2026-07-02)
- **Severity:** HIGH — supply-chain remote code execution vector. The sandbox contains
  blast radius (cap_drop=ALL, no-new-privileges, read-only rootfs, egress proxy), but
  arbitrary attacker code running at all is the exact vector the control claims to kill.

## Title
The container-level `npm_config_ignore_scripts=true` never reached the `npx` child that
actually fetches untrusted packages, so preinstall/install/postinstall lifecycle scripts
of a registered stdio MCP server's npm package executed on fetch.

## Reproduction (code trace — no live docker needed to prove the env gap)
1. `services/mcp-broker/src/sandbox/docker_manager.py:418` sets
   `npm_config_ignore_scripts=true` in the **container** environment.
2. The sandbox agent spawns each stdio MCP server in
   `sandbox-image/agent/stdio_manager.py:336`:
   `asyncio.create_subprocess_exec(command, *args, env=proc_env, ...)`.
   An explicit `env=` **replaces** the process environment (Python does not merge the
   parent `os.environ`).
3. `proc_env = _build_child_env(requested_env, ...)`
   (`shared/ai_mesh_shared/mcp_stdio_common.py:119`) rebuilds the child env FRESH from
   `_SAFE_ENV_PASSTHROUGH` (lines 81–97). That allowlist does **not** include
   `npm_config_ignore_scripts`.
4. There is **no baked `.npmrc`** in `services/mcp-broker/sandbox-image/` enforcing
   ignore-scripts (confirmed by grep/find), so the env var was the sole mechanism.
5. => the `npx` child ran with `ignore-scripts` unset → npm default **false** → install
   lifecycle scripts of the (untrusted, tenant-registered) package executed.
   Secondary: a server-spec `env: {"npm_config_ignore_scripts": "false"}` would also have
   overridden a passed-through value (the server-spec merge precedes the return).

## Expected
Every spawned stdio child (the only place untrusted npm packages are fetched + executed)
runs with npm install lifecycle scripts disabled, un-overridable by server-spec or host env.

## Actual (before fix)
Lifecycle scripts enabled (npm default) for the spawned child; the container-level flag was
inert for that process.

## Root cause
Env-isolation correctness bug: `_build_child_env` intentionally rebuilds a minimal child env
from an allowlist (good for secret isolation) but the supply-chain safety flag was assumed to
flow from the container env — it does not, because the child env is a replacement, not a merge.

## Fix (root cause, fail-closed)
`shared/ai_mesh_shared/mcp_stdio_common.py:_build_child_env` now force-pins
`child["npm_config_ignore_scripts"] = "true"` **unconditionally and last** (after the
server-spec merge + denylist strip), mirroring the existing `MCP_REMOTE_CONFIG_DIR` pin so
no server-spec/host env can re-enable scripts. The package `bin` (the MCP server) still runs;
only install-time scripts are blocked, so legitimate servers are unaffected.

## Verification
- `cd services/mcp-broker && PYTHONPATH="$PWD/src:$PWD/../../shared:$PWD/sandbox-image/agent" .venv/bin/python -m pytest tests/test_stdio_common.py -q` → **19 passed**
  (default child ignore-scripts=true; server-spec `false`/``/`0`/`no`/`FALSE` all forced to
  true; host-env false forced to true).
- Full broker suite → **101 passed**. Gateway sweep `ai_mesh_gateway/tests` → **1092 passed, 0 failed**.

## Residual (item 8 still open — not marked [x])
1. Enable pinning in prod (`MCP_STDIO_REQUIRE_PINNED_PACKAGES=true`) — requires pinning every
   registered server spec (per CHG-0022).
2. Bake a locked `.npmrc` / private registry into the sandbox image (registry still default
   public npmjs).
3. End-to-end "malicious-postinstall fixture proven inert via egress capture" — needs a
   dedicated host with docker + real package fetch + egress capture.
