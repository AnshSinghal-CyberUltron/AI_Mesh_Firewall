# CHG-0146 — broker agent-RPC timeout had no upper bound (caller-supplied timeout → unbounded connection hold / DoS)

**Change-id:** CHG-0146
**Date:** 2026-07-03
**Severity:** LOW-MEDIUM (resource-exhaustion / DoS containment — the broker folded a caller-supplied
`body.timeouts` into the effective agent-RPC timeout via `max(...)` with NO ceiling, so an oversized value
would hold a broker→agent httpx connection + the serving worker open for that whole duration; under
concurrency that exhausts the broker's connection pool / event-loop capacity).
**Area:** HARDEN THE ARCHITECTURE — resource limits / DoS bounds (items 10 & 17); the broker self-defending
against an untrusted-influenced input (parity with the gateway body cap CHG-0034/0063/0140).
**Files:** `services/mcp-broker/src/sandbox/routes.py` (`_AGENT_TIMEOUT_MAX`, `_forward_sandbox_rpc`);
`services/mcp-broker/tests/test_sandbox_routes.py` (+2 tests).
**Whose work it touches:** the broker sandbox RPC forwarder (architecture-hardening surface).

## Root cause

`_forward_sandbox_rpc` computes the per-RPC timeout it passes to `_post_agent_rpc`
(`httpx.AsyncClient(timeout=timeout)`) as:

```python
timeout = _AGENT_TIMEOUT                       # default 130s
if body.timeouts:
    timeout = max(
        timeout,
        body.timeouts.get("init_seconds", 0),   # caller-supplied
        body.timeouts.get("method_seconds", 0), # caller-supplied
    )
```

`body.timeouts` is a `dict[str, float]` on the request body (`SandboxRpcRequest`). The `max(...)` folds the
caller's `init_seconds`/`method_seconds` in with **no upper bound**. In normal operation the gateway sends
`init_seconds=120` / `method_seconds≈60` (env config, so the effective timeout is ~130s), but the broker
**trusts the caller** to send sane values. A misconfigured gateway (`MCP_STDIO_INIT_TIMEOUT` set huge), a
buggy caller, or any non-gateway caller that reaches the broker could set `init_seconds=99999` → the broker
holds the agent connection + the request-serving coroutine open for ~28 hours. A handful of such RPCs
exhausts the broker's httpx connection pool / event-loop capacity → availability DoS for all orgs.

The broker must bound this itself (defense-in-depth), the same way the gateway caps inbound bodies rather
than trusting the backend.

## The fix (CHG-0146)

A hard ceiling on the effective timeout:

```python
_AGENT_TIMEOUT_MAX = max(
    _AGENT_TIMEOUT,
    float(os.environ.get("MCP_BROKER_AGENT_TIMEOUT_MAX", "900")),   # 15 min
)
...
if timeout > _AGENT_TIMEOUT_MAX:
    LOG.warning("sandbox rpc org=%s: requested agent timeout %.0fs exceeds ceiling %.0fs; clamping", ...)
    timeout = _AGENT_TIMEOUT_MAX
```

- Clamped to `min(computed, _AGENT_TIMEOUT_MAX)`. The ceiling is `max(_AGENT_TIMEOUT, env)` so an operator's
  explicit `MCP_BROKER_AGENT_TIMEOUT` base is never clipped.
- 900s (15 min) default is generous — well above any legitimate slow cold-start (npx fetch) or long tool
  call — so no real call is affected; only pathological values are trimmed.
- A clamp event is logged (safe metadata only) for observability.

## Verification

```
cd services/mcp-broker
./.venv/bin/python -m pytest tests/test_sandbox_routes.py -q   # 16 passed (2 new)
./.venv/bin/python -m pytest tests -q -k "not websocket"       # 172 passed, 0 failed
```

New tests capture the `timeout` `httpx.AsyncClient` is constructed with on `/v1/sandbox/{org}/stdio/rpc`:
`test_agent_rpc_timeout_clamped_to_ceiling` (`init_seconds=method_seconds=99999` → effective timeout ==
`_AGENT_TIMEOUT_MAX`, and 99999 never used); `test_agent_rpc_normal_timeout_not_clamped`
(`init=120`/`method=60` → effective == the base `_AGENT_TIMEOUT`, unclamped).

## Scope / honesty note

Broker-side DoS bound; no change to the normal timeout behaviour (legitimate calls ≤ ceiling pass
unchanged). In current deployments the gateway already sends bounded values, so this closes a
defense-in-depth / misconfiguration / non-gateway-caller gap rather than a live exploit. Does not change the
host-blocked live-stress status (the real 300–500-sandbox concurrency drill still needs a dedicated host).
Partial coverage is not completion.
