# P4.13 / P6.18 — RECHECK iter28 (PARTIAL — still BLOCKED on §3)

**Iteration:** cursor-ralph-iter28  
**Date:** 2026-07-02

## Verdict

**PARTIAL PROGRESS, still BLOCKED** — Checklist §1 (broker unified route) and §2
(`broker_send_rpc`) remain **LANDED in source + live**. §3 (`mcp_proxy` routes all
transports through sandbox) is **NOT landed** — zero `broker_send_rpc` references in
`mcp_proxy.py`; **14** direct `httpx.AsyncClient` upstream dial sites remain. Cannot mark
P4.13/P6.18/P6.19-P4 `[x]`.

## Live probes (2026-07-02 iter28)

| Probe | URL | HTTP | vs iter27 |
|-------|-----|------|-----------|
| Unified broker RPC | `POST :8311/v1/sandbox/zeroshield/rpc` | **401** (auth required) | unchanged |
| Stdio broker RPC | `POST :8311/v1/sandbox/zeroshield/stdio/rpc` | **401** | unchanged |
| Broker health | `GET :8311/health` | 200 | unchanged |

## Code grep (gateway + broker)

| Symbol / route | Expected | Actual (iter28) |
|----------------|----------|-------------------|
| `broker_send_rpc` | In `mcp_sandbox_client.py` | **PRESENT** (`:199-256`) |
| `POST /{org}/rpc` broker route | Unified transport-agnostic | **PRESENT** (`routes.py:296`) |
| `mcp_proxy.py` uses `broker_send_rpc` | All http/sse/ws/stdio via sandbox | **MISSING** — 0 references |
| `mcp_proxy_direct_httpx` wiring gate | false | **true** |
| Direct httpx in mcp_proxy | 0 upstream dials | **14** `httpx.AsyncClient` sites |

```bash
grep -r broker_send_rpc gateway/ai_mesh_gateway/mcp_proxy.py  # → no matches
grep -c httpx.AsyncClient gateway/ai_mesh_gateway/mcp_proxy.py  # → 14
```

## Cursor-owned gates (iter28)

| Gate | Result |
|------|--------|
| P7.23 N2+N3 package tests | **26/26** passed |
| Broker + agent combined | **138/138** passed |
| Frontend build | **PASS** |
| `mcp_sandbox_transport_verify.py` wiring | **BLOCKED** (exit 2) |
| P10 recursive (3×, SKIP_PLAYWRIGHT) | **ALL GREEN** (retry w/ INTER_HARNESS_SLEEP=12) — broker 97, agent 41, multi-org/concurrency/load/leakage/oauth PASS ×3. First attempt: round 2 load FAIL (1/1440 transient control -32000). |

## Cursor prep (iter28)

- Enhanced `TRANSPORT_MANIFEST.example.json` with HTTP/SSE/WS slug placeholders + per-transport hints.
- Added `mcp-parallel/findings/p4-13/TRANSPORT_REGISTRATION.md` (post-§3 registration steps).
- `mcp_sandbox_transport_verify.py` now reports httpx site count + broker_send_rpc ref count and prints registration guide when BLOCKED.

## 15-MCP fleet

Manifest `scripts/ralph/.mcp_scale_manifest.json`: **3 orgs × 5 stdio Everything servers** (zeroshield, org-a, org-b). Headed browser available (noVNC :6080, CDP :9222) for manual HTTP OAuth when §3 lands.

## Unblock (Claude §3)

Land `mcp_proxy` routing per `docs/mcp/gateway-integration-checklist.md` §3
(replace direct httpx for streamable-http, sse, websocket with `broker_send_rpc`),
then:

```bash
# 1. Register 4 transports per TRANSPORT_REGISTRATION.md
TRANSPORT_MANIFEST=... ROUNDS=3 python3 scripts/mcp_sandbox_transport_verify.py
# 2. tcpdump network assertion per script guide
ROUNDS_GATE=3 python3 scripts/mcp_p10_recursive_gate.py
# 3. Playwright B1/B2/B4
```

Hive memory: `mcp-gateway/cursor-ralph-iter28-p4.13-recheck`
