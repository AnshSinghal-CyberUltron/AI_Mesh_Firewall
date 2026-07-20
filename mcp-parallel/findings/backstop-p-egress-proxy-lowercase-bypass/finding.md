# CHG-0125 — egress-lockdown bypass: proxy env set uppercase-only (curl/wget/git escape the proxy)

**Change-id:** CHG-0125
**Date:** 2026-07-03
**Severity:** MEDIUM (architecture — egress-lockdown guardrail bypass → data-exfiltration path from inside a sandbox). Latent: only active when `MCP_SANDBOX_EGRESS_LOCKDOWN=true` (or an explicit `MCP_SANDBOX_HTTP_PROXY`), but on that path it silently defeats the lockdown for a whole class of tools.
**Area:** HARDEN THE ARCHITECTURE — gVisor + …/egress-lockdown. The per-org sandbox's outbound traffic must funnel through the egress proxy; the proxy is the enforcement point for the outbound allowlist.
**Files:** `services/mcp-broker/src/sandbox/docker_manager.py` (`_egress_proxy_env`); `services/mcp-broker/tests/test_sandbox_lifecycle.py` (extended `test_egress_lockdown_injects_proxy_env` + new `test_no_egress_proxy_env_when_lockdown_off_and_no_proxy`).
**Whose work it touches:** the owning-session broker sandbox egress config (`_egress_proxy_env`, `SandboxDockerConfig`). No gateway/control change.

## Root cause

`_egress_proxy_env` injected the egress proxy into the sandbox container using **only the UPPERCASE** variables:

```python
return {
    "HTTP_PROXY": http_proxy,
    "HTTPS_PROXY": https_proxy,
    "NO_PROXY": self.config.no_proxy,
}
```

Docker propagates these to every process in the container, including the stdio MCP servers the agent spawns and any subprocess they exec. But several ubiquitous outbound tools honor **only the lowercase** proxy variables:

- **curl** deliberately ignores uppercase `HTTP_PROXY` for plain-HTTP requests — its man page states *"http_proxy … is only used in lowercase"* (a hardening reaction to the httpoxy class, CVE-2016-5385, where a CGI `HTTP_PROXY` from a request header could hijack outbound calls). curl uses lowercase `http_proxy` for HTTP.
- **wget** reads lowercase `http_proxy` / `https_proxy`.
- **git** performs HTTP(S) via curl and inherits the same behavior.

So with only the uppercase vars set, a sandboxed MCP server (or any subprocess) that shells out to **curl / wget / git** for outbound HTTP would **bypass the egress proxy entirely** and connect directly to arbitrary external hosts over the org bridge network's NAT — even when `MCP_SANDBOX_EGRESS_LOCKDOWN=true`. That is a direct data-exfiltration channel that the egress-lockdown guardrail is meant to close, and it also escapes the proxy's outbound allowlist.

## The fix (CHG-0125)

`_egress_proxy_env` now emits **both** the upper- and lower-case variants (`HTTP_PROXY`+`http_proxy`, `HTTPS_PROXY`+`https_proxy`, `NO_PROXY`+`no_proxy`), which is the standard defensive convention for a container egress proxy — curl/wget/git and case-sensitive libraries all now route through the proxy. Behavior is otherwise unchanged; the `{}`-when-disabled fast path is preserved.

## Verification

```
cd services/mcp-broker
./.venv/bin/python -m pytest tests/test_sandbox_lifecycle.py -q -k "egress or proxy"   # 2 passed
./.venv/bin/python -m pytest tests -q -k "not websocket"                                # 158 passed
```

- `test_egress_lockdown_injects_proxy_env` now also asserts `http_proxy` / `https_proxy` (lowercase) and `no_proxy == NO_PROXY`.
- `test_no_egress_proxy_env_when_lockdown_off_and_no_proxy` locks the fast path: with lockdown off and no explicit proxy, **neither** case is injected (the absence stays unambiguous — it can't be half-set).

`_egress_proxy_env` is the ONLY proxy-env source in the broker/sandbox-image/gateway (grep-verified), so no sibling spot carries the same bug.

## Scope / honesty note

This closes the *soft* (env-var) egress bypass for proxy-respecting tools. It does NOT convert egress into a *hard* network lockdown: the org network is a `driver=bridge` (non-`internal`) network with NAT, and a malicious server that makes raw socket connections while ignoring ALL proxy env vars still egresses directly. A hard lockdown (internal network + forced-transparent proxy or iptables egress filtering) remains an INFRA task (needs a running proxy + network-topology change; not unit-gatable here) and is unchanged by this fix. Egress-lockdown is also OFF by default (`MCP_SANDBOX_EGRESS_LOCKDOWN` opt-in). Does not change the host-blocked live-stress status (items 14–19). Partial coverage is not completion.
