# CHG-0136 — sandbox agent /rpc was unauthenticated (network isolation was the SOLE cross-tenant control)

**Change-id:** CHG-0136
**Date:** 2026-07-03
**Severity:** MEDIUM (cross-tenant defense-in-depth). The in-sandbox agent's `/rpc` endpoint had **no authentication** — it relied entirely on Docker network isolation. If that isolation is ever compromised (a host-run-broker shared-bridge topology, a per-org-network misconfig, or a Docker networking bug), a sibling sandbox could reach and call another org's agent **unauthenticated** — invoking that org's MCP servers / reading its data. No second layer. Directly relevant to "cross-tenant canaries never observed on any channel."
**Area:** HARDEN 1.4 / architecture — per-tenant isolation, no single point of failure; the broker→agent hop of the authz chain.
**Files:** `services/mcp-broker/sandbox-image/agent/main.py` (`/rpc` verify); `services/mcp-broker/src/sandbox/routes.py` (`_post_agent_rpc` send); `services/mcp-broker/src/sandbox/docker_manager.py` (`_run_kwargs` provision); `shared/ai_mesh_shared/mcp_stdio_common.py` (`_SECRET_ENV_DENYLIST`); tests: `agent/tests/test_rpc_unified.py`, `tests/test_sandbox_lifecycle.py`, `tests/test_agent_ready_retry.py`.
**Whose work it touches:** the owning-session agent `/rpc` + broker `_post_agent_rpc` + docker_manager env, and the shared child-env builder.

## Root cause

Production isolates sandboxes on **per-org Docker networks** (`mcp_sandbox_net_{org}`), so cross-tenant reach is
normally impossible. But the agent's `/rpc` handler verified **nothing** — no key, no caller identity — so
network isolation was the *only* thing preventing one sandbox from calling another's agent. Two ways that single
control can fail:

- **Host-run broker**: `docker_manager._run_kwargs` places sandboxes on the *shared* bridge (`self.config.network`)
  when the broker runs on the host (no container ref) — where every sandbox can reach every other's agent port.
- A per-org-network misconfiguration or a Docker networking bug.

In any of those, an unauthenticated `/rpc` is directly cross-tenant-callable.

## The fix (CHG-0136) — opt-in broker→agent key (defense-in-depth)

An optional `MCP_AGENT_INTERNAL_KEY`, enforced end-to-end:

1. **agent** (`main.py`): if `MCP_AGENT_INTERNAL_KEY` is set, `/rpc` requires a matching `X-Sandbox-Agent-Key`
   header (constant-time `hmac.compare_digest`); mismatch → JSON-RPC `-32001 unauthorized`. Unset → allow
   (backward-compatible).
2. **broker** (`_post_agent_rpc`): attaches `X-Sandbox-Agent-Key` (from the broker's own `MCP_AGENT_INTERNAL_KEY`)
   when configured. (`headers=_headers or None` preserves the "no headers → None" contract.)
3. **docker_manager** (`_run_kwargs`): provisions the SAME key into the sandbox env so the agent verifies against
   the value the broker sends.
4. **shared denylist**: `MCP_AGENT_INTERNAL_KEY` added to `_SECRET_ENV_DENYLIST`, and it is not in
   `_SAFE_ENV_PASSTHROUGH` — so the key is **never** passed to a spawned MCP server (`_build_child_env` builds a
   clean allowlisted env). **This is what makes a single broker-wide key a real cross-tenant control**: a
   malicious MCP server inside sandbox A cannot read the key, so it cannot forge an authenticated call to sandbox
   B's agent even on a shared network.

Opt-in + backward-compatible: unset everywhere → no change; set → all three parts activate consistently (same
value). A mixed rollout (old sandboxes without the env + new with it) is safe — old agents don't require the key,
new ones do, and the broker sends it either way.

## Verification

```
cd services/mcp-broker
./.venv/bin/python -m pytest tests -q -k "not websocket"                      # 166 passed
cd sandbox-image/agent && ../../.venv/bin/python -m pytest tests -q           # full agent suite green
```

New tests: agent `/rpc` — missing/wrong key → `-32001`, correct key → dispatches, unset → allowed;
`_run_kwargs` provisions the key when set / omits it when unset; `_post_agent_rpc` sends `X-Sandbox-Agent-Key`
when set / no header when unset (and the CHG-0121 `X-Request-ID` tests still pass). Byte-probe: `_build_child_env`
**refuses** to pass `MCP_AGENT_INTERNAL_KEY` to a child even when a server-spec/host env tries to smuggle it
(denylist).

## Scope / honesty note

Defense-in-depth layer for the broker→agent hop; network isolation remains the primary control (unchanged). The
key is a single broker-wide secret — sufficient for cross-tenant because spawned servers can't read it; a
per-sandbox key would add marginal protection against a compromised *agent* process (out of scope). Opt-in, so
it only activates when an operator sets `MCP_AGENT_INTERNAL_KEY`. Unit-level; end-to-end at scale is host-blocked
(items 14–19). Partial coverage is not completion.
