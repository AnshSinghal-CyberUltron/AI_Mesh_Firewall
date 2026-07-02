# P4.13 / P6.18 — RECHECK iter27 (PARTIAL — still BLOCKED on §3)

**Iteration:** cursor-ralph-iter27  
**Date:** 2026-07-02

## Verdict

**PARTIAL PROGRESS, still BLOCKED** — Checklist §1 (broker unified route) and §2
(`broker_send_rpc`) remain **LANDED in source + live**. §3 (`mcp_proxy` routes all
transports through sandbox) is **NOT landed** — zero `broker_send_rpc` references in
`mcp_proxy.py`; 14+ direct `httpx.AsyncClient` upstream dials remain. Cannot mark
P4.13/P6.18/P6.19-P4 `[x]`.

## Live probes (2026-07-02 iter27)

| Probe | URL | HTTP | vs iter26 |
|-------|-----|------|-----------|
| Unified broker RPC | `POST :8311/v1/sandbox/zeroshield/rpc` | **401** (auth required) | unchanged |
| Stdio broker RPC | `POST :8311/v1/sandbox/zeroshield/stdio/rpc` | **401** | unchanged |
| Broker health | `GET :8311/health` | 200 | unchanged |

## Code grep (gateway + broker)

| Symbol / route | Expected | Actual (iter27) |
|----------------|----------|-----------------|
| `broker_send_rpc` | In `mcp_sandbox_client.py` | **PRESENT** (`:199-256`) |
| `POST /{org}/rpc` broker route | Unified transport-agnostic | **PRESENT** (`routes.py:296`) |
| `mcp_proxy.py` uses `broker_send_rpc` | All http/sse/ws/stdio via sandbox | **MISSING** — zero references |
| `mcp_proxy_direct_httpx` wiring gate | false | **true** (`mcp_sandbox_transport_verify.py`) |
| Direct httpx in mcp_proxy | 0 upstream dials | **14** `httpx.AsyncClient` sites |

## Cursor-owned gates (green)

| Gate | Result |
|------|--------|
| P7.23 N2+N3 package tests | **26/26** passed |
| Broker + agent combined | **138/138** passed |
| Frontend lint + build | **PASS** |
| `mcp_sandbox_transport_verify.py` wiring | **BLOCKED** (exit 2) |
| P10 recursive (1×, SKIP_PLAYWRIGHT) | **ALL GREEN** — broker 97, agent 41, multi-org GREEN, concurrency/load/leakage/oauth PASS |

## 15-MCP fleet

Manifest `scripts/ralph/.mcp_scale_manifest.json`: **3 orgs × 5 stdio Everything servers** (zeroshield, org-a, org-b). Headed browser available (noVNC :6080, CDP :9222) for manual HTTP OAuth when §3 lands.

## Unblock (Claude §3)

Land `mcp_proxy` routing per `docs/mcp/gateway-integration-checklist.md` §3
(replace direct httpx for streamable-http, sse, websocket with `broker_send_rpc`),
then:

```bash
ROUNDS=3 python scripts/mcp_sandbox_transport_verify.py
# + tcpdump network assertion per script guide
ROUNDS_GATE=3 python scripts/mcp_p10_recursive_gate.py
```

Hive memory: `mcp-gateway/cursor-ralph-iter27-p4.13-recheck`
