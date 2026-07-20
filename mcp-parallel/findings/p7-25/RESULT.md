# P7.25 — Per-org credential/env isolation

**Iteration:** cursor-ralph-iter18  
**Date:** 2026-07-02  
**Status:** VERIFIED ✅ (adversarial tests all green)

## Isolation mechanisms in place

### 1. Child process env isolation (`mcp_stdio_common.py`)
- `_SECRET_ENV_DENYLIST`: 20+ gateway-internal secrets stripped from every `_build_child_env` call
- Both `env` dict (caller-supplied BYOK) and `host_environ` (host OS env) are sanitized
- `LD_PRELOAD` and `DYLD_INSERT_LIBRARIES` explicitly stripped (library injection vector)
- `MCP_REMOTE_CONFIG_DIR` is set to a per-org path (`/tmp/mcp-orgs/{org_slug}/mcp-auth` in gateway path)
- Allowlist-only host passthrough (`_SAFE_ENV_PASSTHROUGH`) — no wildcard inheritance

### 2. Docker resource isolation (`docker_manager.py`)
- **Network**: `mcp_sandbox_net_{org_slug}` — per-org bridge; containers cannot reach sibling agent ports
- **Volume**: `mcp_sandbox_{org_slug}_auth` — per-org named volume mounted at `/data/mcp-auth`; different volumes → different filesystems → no cross-org token access
- **Container**: `{org_slug}-mcp-sandbox` — one container per org
- **Labels**: `ai_mesh.org_slug={org_slug}` on every container — enables restart-safe orphan re-adoption

### 3. In-container env (`docker_manager._run_kwargs`)
- `ORG_SLUG` scopes the container to its org
- `MCP_REMOTE_CONFIG_DIR=/data/mcp-auth` — per-container (different volume backing each org)
- `npm_config_ignore_scripts=true` — postinstall supply-chain guard (N1, iter17)
- No gateway secrets injected into container env

## Key finding (iter18)

`MCP_REMOTE_CONFIG_DIR` is `/data/mcp-auth` (same path) in all containers — this is **correct**. The isolation is via distinct Docker volumes, not distinct paths. A test asserting different paths would be wrong.

## Adversarial tests added

| Test | File | Attack scenario |
|------|------|----------------|
| `test_cross_org_env_isolation` | `test_stdio_common.py` | Org A BYOK (LINEAR_API_KEY, GITHUB_TOKEN) absent in Org B env; Org B BYOK absent in Org A env; MCP_REMOTE_CONFIG_DIR unique |
| `test_ld_preload_stripped_from_requested_env` | `test_stdio_common.py` | Attacker passes LD_PRELOAD=/evil/lib.so in env — stripped |
| `test_ld_preload_stripped_from_host_env` | `test_stdio_common.py` | Host LD_PRELOAD not on allowlist — excluded |
| `test_dyld_insert_libraries_stripped` | `test_stdio_common.py` | macOS library injection vector — stripped |
| `test_all_denylist_vars_absent_in_child` | `test_stdio_common.py` | All 20+ denylist vars in `env` → all absent from child |
| `test_all_denylist_vars_absent_even_in_host_env` | `test_stdio_common.py` | All denylist vars in host environ → all absent from child |
| `test_remote_config_dir_unique_per_org` | `test_stdio_common.py` | 4 different orgs → 4 different MCP_REMOTE_CONFIG_DIR values |
| `test_per_org_network_names_are_distinct` | `test_sandbox_lifecycle.py` | 4 orgs → 4 distinct Docker network names |
| `test_per_org_volume_names_are_distinct` | `test_sandbox_lifecycle.py` | 4 orgs → 4 distinct volume names |
| `test_per_org_container_names_are_distinct` | `test_sandbox_lifecycle.py` | 4 orgs → 4 distinct container names |
| `test_org_slug_in_sandbox_environment` | `test_sandbox_lifecycle.py` | ORG_SLUG env set correctly in container |
| `test_auth_volume_is_org_specific_for_credential_isolation` | `test_sandbox_lifecycle.py` | Org A and Org B get different volumes mounted at /data/mcp-auth |
| `test_sandbox_labels_include_org_slug_for_isolation` | `test_sandbox_lifecycle.py` | Labels enable restart-safe orphan re-adoption |

## Gate

`cd services/mcp-broker && .venv/bin/python -m pytest tests/ -q` → **94 passed / 0 failed** (was 81 before +13 new tests)
