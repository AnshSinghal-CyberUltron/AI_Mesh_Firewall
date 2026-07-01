# MCP Gateway Per-Tenant Sandbox — Implementation Plan

**Status:** Approved architecture (user-confirmed 2026-06-29)  
**Scope:** Phase 1 MVP — stdio transport only; HTTP/SSE/WebSocket unchanged  
**Primary deliverable owner:** Platform / gateway team

---

## Executive Summary

Today, stdio MCP servers run as **child processes inside the gateway container** (`mcp_stdio_adapter.py` L487–496 via `asyncio.create_subprocess_exec`). That shares the gateway's filesystem, network namespace, and (without careful env filtering) risks cross-tenant credential bleed. The gateway image already embeds Node/npx (`gateway/Dockerfile` L28–32) and docker-compose mounts shared npm/uv caches (`docker-compose.yml` L206–208).

This plan moves stdio execution to **one long-lived Docker sandbox per org (tenant)**, orchestrated by extending **`services/mcp-broker`** into a **Sandbox Controller**. The gateway keeps all policy, scanning, and audit logic in `mcp_proxy.py`; only the spawn/stdio I/O layer is delegated. Local dev retains the current in-process path behind `MCP_STDIO_IN_PROCESS=true`.

**Phase 1 runtime recommendation:** standard **Docker (runc)** containers with cgroup limits. **Phase 2 hardening:** **gVisor (`runsc`)** on Linux production hosts. **Firecracker** is deferred unless VM-grade isolation becomes a compliance requirement.

---

## Confirmed Architecture Decisions

| # | Decision | Implication |
|---|----------|-------------|
| 1 | **One sandbox per tenant (org)** | Container key = `org_slug`; multiple stdio MCP servers per org run as processes inside the same container via an in-sandbox agent |
| 2 | **Dev mode: in-process stdio** | `MCP_STDIO_IN_PROCESS=true` → existing `mcp_stdio_adapter.py` path unchanged; prod default `false` |
| 3 | **No npm package restrictions** | Remove/ignore `MCP_STDIO_PACKAGE_ALLOWLIST` enforcement in sandbox path; backend registration remains the authorization gate |
| 4 | **Extend `mcp-broker`, not new service** | `MCP_BROKER_URL` (`docker-compose.yml` L187) becomes Sandbox Controller base URL; no second service |
| 5 | **Full outbound network** | Sandbox containers get unrestricted egress (NAT); security boundary is process/container isolation + gateway scan/audit, not egress denylist |

---

## Target Architecture

```mermaid
flowchart TB
    subgraph Client
        VS[VS Code / Agent SDK]
    end

    subgraph Gateway["gateway (ai_mesh_gateway)"]
        MP[mcp_proxy.org_mcp_jsonrpc]
        SCAN[mcp_scan_orchestrator]
        RL[rate_limit_enforcement]
        ADP[mcp_stdio_adapter OR broker client]
    end

    subgraph Control["control (Django)"]
        REG[MCPServerRegistration]
        AUD[MCPEvent ingestion]
    end

    subgraph Broker["mcp-broker (Sandbox Controller)"]
        API[Sandbox API]
        LC[Lifecycle: ensure / reaper / destroy]
        DK[Docker SDK]
    end

    subgraph Sandboxes["Per-org containers"]
        SA1[sandbox-agent org-a]
        SA2[sandbox-agent org-b]
        P1[npx MCP processes]
    end

    VS -->|POST /gateway/{org}/mcp/{server}| MP
    MP --> RL
    MP --> SCAN
    MP --> REG
    MP --> ADP
    ADP -->|HTTP if not in-process| API
    API --> DK
    DK --> SA1 & SA2
    SA1 --> P1
    MP -->|_record_gateway_event| AUD
```

**ASCII (deployment view):**

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────────────────────┐
│   Client    │────▶│  gateway:8300    │────▶│  mcp-broker:8311            │
│  (MCP JSON) │     │  mcp_proxy.py    │     │  Sandbox Controller         │
└─────────────┘     │  scan + audit      │     │  Docker socket (Linux prod) │
                    └────────┬───────────┘     └──────────────┬──────────────┘
                             │                                 │
                             │ control:8000                    │ docker run
                             ▼                                 ▼
                    ┌──────────────────┐     ┌─────────────────────────────┐
                    │  MCPEvent audit  │     │  mcp-sandbox-{org_slug}     │
                    └──────────────────┘     │  sandbox-agent + npx procs  │
                                             │  full egress, no gw secrets │
                                             └─────────────────────────────┘
```

---

## gVisor vs Firecracker — Analysis for This Workload

### Workload profile

| Attribute | Value |
|-----------|-------|
| Process model | Long-lived per-org container; lazy-spawned npx/Node stdio MCP children |
| Package source | Arbitrary npm via `npx -y` at connect time (cold fetch on first use) |
| Network | **Full outbound** — tools may call external APIs |
| Host stack | Docker Compose today; likely Kubernetes later |
| Dev host | macOS (Docker Desktop) — **no KVM, no runsc** |
| Density target | Many tenants, moderate concurrent orgs |
| Threat model | Untrusted tenant-supplied MCP packages must not read gateway secrets or affect other tenants |

### gVisor (`runsc`)

**What it is:** User-space kernel (Sentry) that intercepts syscalls for OCI containers. Integrates with Docker as an alternate OCI runtime (`--runtime=runsc`).

| Dimension | Assessment |
|-----------|------------|
| **Startup latency** | Near-container (~100ms–2s). No VM boot. Cold `npx` fetch dominates, not runtime |
| **Node/npm compatibility** | Generally good; occasional syscall gaps (e.g. some `io_uring`, exotic `epoll` edge cases). Widely used for untrusted containers |
| **Operational complexity** | Low–medium: install `runsc`, set Docker default or per-container runtime. Works in K8s via `runtimeClassName: gvisor` |
| **macOS dev** | **Not available** on Docker Desktop. Dev stays on runc/in-process |
| **Memory overhead** | ~50–150 MiB per sandbox vs runc (Sentry process) |
| **Density** | Good for hundreds of sandboxes per host |
| **Security model** | Syscall interception; host kernel still present but most syscalls never reach it. Weaker than VM isolation, stronger than namespaces alone |
| **Docker Compose fit** | Excellent — `runtime: runsc` on sandbox service template |
| **Full egress** | Supported; gVisor does not restrict network by default |

### Firecracker

**What it is:** Lightweight microVM (KVM). Each sandbox is a VM with its own guest kernel. Typically accessed via `firecracker-containerd`, Kata Containers, or cloud-managed (Fly Machines, Lambda).

| Dimension | Assessment |
|-----------|------------|
| **Startup latency** | Higher than containers: VM boot + agent (~125ms–1s+ optimized; still slower than runc for warm pools) |
| **Node/npm compatibility** | Excellent — full Linux guest kernel; no syscall emulation gaps |
| **Operational complexity** | **High** for this repo: requires KVM, custom containerd shim or Kata, not a simple `docker run` extension |
| **macOS dev** | **Not available** (no KVM in Docker Desktop) |
| **Memory overhead** | Higher: guest kernel + mini-OS per VM (~128–256 MiB baseline per sandbox) |
| **Density** | Lower than gVisor/runc at same RAM budget |
| **Security model** | Hardware virtualization boundary — strongest isolation of the two |
| **Docker Compose fit** | **Poor** — not a drop-in Docker runtime; needs Kata/Firecracker stack |
| **Full egress** | Supported via host networking / CNI |

### Decision matrix

| Criterion (weight) | Docker runc | gVisor runsc | Firecracker |
|--------------------|-------------|--------------|-------------|
| Fits mcp-broker Docker SDK model (high) | ✅ Best | ✅ Good (`--runtime=runsc`) | ❌ Needs different stack |
| Node/npx syscall compat (high) | ✅ Best | ✅ Good (watch edge cases) | ✅ Best |
| Startup / warm latency (high) | ✅ Best | ✅ Good | ⚠️ Slower cold VM |
| macOS dev parity (medium) | ✅ Same as prod pattern | ❌ Linux-only | ❌ Linux-only |
| Multi-tenant isolation (high) | ⚠️ Namespaces only | ✅ Syscall sandbox | ✅ VM boundary |
| Ops complexity (medium) | ✅ Lowest | ✅ Moderate | ❌ Highest |
| Tenant density (medium) | ✅ Best | ✅ Good | ⚠️ Lower |
| K8s migration path (low) | ✅ | ✅ runtimeClass | ✅ Kata/Firecracker |

### Recommendation

| Phase | Runtime | Rationale |
|-------|---------|-----------|
| **Phase 1 (MVP)** | **Docker runc** + cgroup limits | Fastest path: `mcp-broker` already planned as Docker orchestrator; maximizes Node/npm compatibility while building lifecycle/API; macOS dev uses in-process fallback so prod/dev divergence is already accepted |
| **Phase 2 (hardening)** | **gVisor (`runsc`)** on Linux prod | Best ROI: material isolation uplift without abandoning Docker Compose/K8s OCI workflow; acceptable density for per-org sandboxes; Firecracker-level isolation not justified until compliance or proven kernel escape concerns |
| **Future (optional)** | **Firecracker via Kata** | Only if SOC2/PCI/customer contract requires VM boundary, or gVisor syscall gaps block specific MCP packages |

**Honest tradeoff:** With **full outbound network**, neither gVisor nor Firecracker stops a malicious MCP package from exfiltrating data the tool can access — isolation primarily protects **host + other tenants + gateway secrets**. Firecracker adds defense-in-depth against container breakout; gVisor adds most of that benefit at lower ops cost for a Docker-centric team.

---

## Component Design

### 1. mcp-broker Sandbox Controller API

**Base URL:** `MCP_BROKER_URL` (already `http://mcp-broker:8311` in `docker-compose.yml` L187)

**Auth:** Internal shared secret `MCP_BROKER_INTERNAL_KEY` (new). Gateway sends `X-MCP-Broker-Key` on every call. Broker rejects missing/invalid key (fail closed).

#### `POST /v1/sandbox/{org_slug}/ensure`

Ensure a sandbox container exists for the org; start if stopped.

**Request:**
```json
{
  "org_id": "uuid-optional-for-labels",
  "warm": true
}
```

**Response 200:**
```json
{
  "org_slug": "acme",
  "container_id": "abc123…",
  "status": "running",
  "agent_url": "http://172.28.0.42:9320",
  "created_at": "2026-06-29T12:00:00Z",
  "last_activity": "2026-06-29T12:00:00Z"
}
```

**Errors:** `503` Docker unavailable; `429` org sandbox quota exceeded.

#### `POST /v1/sandbox/{org_slug}/stdio/rpc`

Proxy one JSON-RPC exchange to a stdio MCP server inside the org sandbox. Broker ensures sandbox, forwards to in-container **sandbox-agent**.

**Request:**
```json
{
  "server_slug": "playwright",
  "command": "npx",
  "args": ["-y", "@playwright/mcp@latest"],
  "env": {"LINEAR_API_KEY": "…"},
  "method": "tools/call",
  "params": {"name": "browser_navigate", "arguments": {"url": "https://example.com"}},
  "jsonrpc_id": 42,
  "timeouts": {
    "init_seconds": 120,
    "method_seconds": 60
  }
}
```

**Response 200:** Full JSON-RPC response object (same shape `mcp_stdio_adapter.send_jsonrpc` returns today).

**Response 502:** Sandbox agent error (mapped from agent body).

#### `GET /v1/sandbox/{org_slug}/status`

**Response:**
```json
{
  "org_slug": "acme",
  "status": "running|stopped|missing",
  "container_id": "…",
  "processes": [
    {"server_slug": "playwright", "running": true, "initialized": true, "last_used": 1719660000}
  ],
  "resource": {"cpu_limit": "1.0", "memory_limit_mb": 2048}
}
```

#### `DELETE /v1/sandbox/{org_slug}`

Destroy container and volumes for org. Idempotent.

**Response 200:** `{"destroyed": true}`

#### `GET /v1/sandboxes` (ops)

List all managed sandboxes (paginated). For future dashboard; not required Phase 1.

#### `GET /health`

Existing (`services/mcp-broker/src/main.py` L8–10); extend with Docker connectivity check.

---

### 2. Sandbox lifecycle

| State | Behavior |
|-------|----------|
| **Missing** | First `ensure` or `stdio/rpc` creates container |
| **Running** | Reused for all org MCP servers; sandbox-agent manages per-server processes (mirror `mcp_stdio_adapter` registry keyed `{org}/{server}`) |
| **Warm** | `ensure(warm=true)` pre-creates container on org MCP registration or first gateway touch (configurable) |
| **Idle reaper** | Broker background task (mirror `mcp_stdio_adapter._reaper_loop` L725–744): if no RPC for `MCP_SANDBOX_IDLE_TIMEOUT` (default 600s, align with `MCP_STDIO_IDLE_TIMEOUT`), `docker stop` container; optional `docker rm` after `MCP_SANDBOX_DESTROY_DELAY` |
| **Hung init** | Sandbox-agent reuses `_HUNG_INIT_TIMEOUT` semantics (180s default from gateway env) |
| **Destroy** | Admin API or org offboarding calls `DELETE`; broker removes container + org volume |

**Concurrency caps (broker-enforced):**

| Env var | Default | Mirrors |
|---------|---------|---------|
| `MCP_SANDBOX_MAX_ORGS` | 50 | cluster-wide running sandboxes |
| `MCP_SANDBOX_MAX_PROCESSES_PER_ORG` | 8 | `MCP_STDIO_MAX_PROCESSES_PER_ORG` L61 |
| `MCP_SANDBOX_MAX_CONCURRENT_INITS` | 4 | `MCP_STDIO_MAX_CONCURRENT_INITS` L64 |

---

### 3. Docker container spec (per tenant)

**Image:** `ai-mesh/mcp-sandbox:latest` (new `services/mcp-broker/sandbox-image/Dockerfile`)

**Base contents:** Node 20 bookworm-slim + Python 3.12 + uv/uvx + npx (same versions as `gateway/Dockerfile` L28–32 for parity).

**sandbox-agent:** Small FastAPI app (extract/refactor from `mcp_stdio_adapter.py` logic) listening on `9320`.

**`docker run` equivalent:**
```bash
docker run -d \
  --name mcp-sandbox-{org_slug} \
  --label ai_mesh.role=mcp-sandbox \
  --label ai_mesh.org_slug={org_slug} \
  --memory 2048m \
  --cpus 1.0 \
  --pids-limit 256 \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=512m \
  --tmpfs /root/.npm:rw,size=1g \
  --tmpfs /root/.cache/uv:rw,size=512m \
  -v mcp_sandbox_{org_slug}_auth:/data/mcp-auth:rw \
  -e MCP_REMOTE_CONFIG_DIR=/data/mcp-auth \
  -e ORG_SLUG={org_slug} \
  --network mcp_sandbox_bridge \
  # Phase 2: --runtime=runsc
  ai-mesh/mcp-sandbox:latest
```

**Explicitly NOT mounted / NOT injected:**

- `GATEWAY_INTERNAL_API_KEY`, `DJANGO_SECRET_KEY`, `DATABASE_URL`, `REDIS_URL` (same denylist as `_SECRET_ENV_DENYLIST` in `mcp_stdio_adapter.py` L136–144)
- Gateway `PYTHONPATH`, host repo mounts
- Docker socket

**BYOK env:** Passed per-RPC in request body; sandbox-agent merges into child env with same `_build_child_env` rules.

**Network:** Attach to `mcp_sandbox_bridge` (broker creates once). `iptables` MASQUERADE for full egress. No link to `gateway` network except broker → agent HTTP on internal bridge.

**Resource defaults (env-configurable on broker):**

| Resource | Default |
|----------|---------|
| Memory | 2048 MiB |
| CPUs | 1.0 |
| Pids | 256 |
| Disk (tmpfs caches) | npm 1GiB, uv 512MiB |

---

### 4. Gateway adapter changes

**New module:** `gateway/ai_mesh_gateway/mcp_sandbox_client.py`

- HTTP client wrapping broker `stdio/rpc` and `ensure`
- Timeouts from existing `MCP_STDIO_*` env vars
- Retries on `503` with exponential backoff (sandbox cold start)

**Changes to `mcp_stdio_adapter.py`:**

```python
# Pseudocode — actual implementation in Phase 1
_IN_PROCESS = os.environ.get("MCP_STDIO_IN_PROCESS", "false").lower() in ("1", "true", "yes")

async def send_jsonrpc(...):
    if _IN_PROCESS:
        return await _send_jsonrpc_in_process(...)  # existing code
    from mcp_sandbox_client import broker_send_jsonrpc
    return await broker_send_jsonrpc(org_slug, server_slug, ...)
```

**Env vars (gateway):**

| Variable | Default (prod compose) | Purpose |
|----------|------------------------|---------|
| `MCP_STDIO_IN_PROCESS` | `false` | `true` = dev in-gateway spawn |
| `MCP_BROKER_URL` | `http://mcp-broker:8311` | Already in `docker-compose.yml` L187 |
| `MCP_BROKER_INTERNAL_KEY` | (required in prod) | Broker auth |
| `MCP_STDIO_*` | unchanged | Timeouts, line bytes — forwarded in RPC body |

**`mcp_proxy.py`:** No change to scan/audit path. `_adapter_forward` (L1685–1747) continues calling `mcp_stdio_adapter.send_jsonrpc`; routing is internal to adapter.

**`gateway/Dockerfile`:** Phase 1 — optionally remove Node/npx from gateway image **after** sandbox stable (Phase 3 cleanup). Not Phase 1 blocker.

---

### 5. Rate limiting on MCP path

**Gap (confirmed):** `org_mcp_jsonrpc` (`mcp_proxy.py` L1754+) has no `enforce_org_tpm_rate_limit` call; chat paths in `main.py` do (e.g. L8891).

**Fix (parallel track, same PR or immediate follow-up):**

At top of `org_mcp_jsonrpc` after `_validate_org_scope`:

```python
rl_block = await _enforce_org_tpm_rate_limit_mcp(request)  # thin wrapper
if rl_block:
    return rl_block  # map 429 to JSON-RPC error envelope
```

Also call `enforce_org_burst_rpm` from `rate_limit_enforcement.py` L117+ for parity with chat.

**MCP-specific token estimate:** `estimated_tokens=5` per JSON-RPC (tools/list cheaper than tools/call); configurable via `MCP_RATE_LIMIT_TOKEN_ESTIMATE`.

**JSON-RPC 429 mapping:**
```json
{
  "jsonrpc": "2.0",
  "id": <msg_id>,
  "error": {"code": -32000, "message": "Rate limit exceeded"}
}
```

---

### 6. Audit / logging integration (unchanged paths)

All policy and audit remain in **`mcp_proxy.py`**:

| Concern | Location | Change |
|---------|----------|--------|
| Inbound scan | `_mcp_security_scan` ~L2020 | None |
| Outbound scan | ~L2122 | None |
| MCPEvent | `_record_gateway_event` ~L2047 | None |
| Key allowlist | `_tool_allowed_by_key` ~L1917 | None |
| Control plane ingest | `POST …/api/mcp-connector/events/gateway/` | None |

Broker and sandbox-agent log **operational** events only (container start/stop, process spawn, stderr tail server-side). Never return stderr/BYOK to client (preserve `mcp_stdio_adapter.py` L274–277 pattern).

---

## Data Flow — stdio MCP `tools/call` (prod)

1. **Client** `POST /gateway/{org}/mcp/{server}` with Bearer API key + JSON-RPC body.
2. **AuthMiddleware** (`middleware.py`) → `AuthContext` with `org_slug`, `mcp_allowed_tools`.
3. **`org_mcp_jsonrpc`** validates org scope (`_validate_org_scope` L1761).
4. **Rate limit** (new) — TPM + burst/RPM via `rate_limit_enforcement.py`.
5. **`_get_server_config`** — transport `stdio`, command/args/env from control plane.
6. **Key allowlist / tool disabled checks** (L1917–2010).
7. **Inbound security scan** on `arguments` (L2020+); block/redact before forward.
8. **`_adapter_forward`** → `mcp_stdio_adapter.send_jsonrpc` (L1705).
9. **Adapter** sees `MCP_STDIO_IN_PROCESS=false` → **`mcp_sandbox_client`** HTTP `POST {MCP_BROKER_URL}/v1/sandbox/{org}/stdio/rpc`.
10. **Broker** ensures container → forwards to **sandbox-agent** inside container.
11. **Sandbox-agent** spawns/reuses `npx` child, writes JSON-RPC to stdin, reads stdout line (8 MiB limit, `MCP_STDIO_MAX_LINE_BYTES`).
12. Response travels back broker → gateway.
13. **Outbound scan** on tool result (L2122+); redact/block if needed.
14. **`_record_gateway_event`** → control plane MCPEvent (L2105+).
15. **JSON-RPC response** returned to client.

---

## Dev vs Prod Modes

| Aspect | Dev (`MCP_STDIO_IN_PROCESS=true`) | Prod (default) |
|--------|-----------------------------------|----------------|
| Stdio execution | Gateway subprocess (`mcp_stdio_adapter.py` L489) | Broker → per-org container |
| mcp-broker | Optional (`profiles: services`) | Required service |
| Docker socket | Not needed on gateway | On **mcp-broker** only |
| macOS Docker Desktop | Works (in-process) | Broker sandbox needs Linux VM backend for Docker-in-Docker OR remote Docker host |
| npm cache | `npm_cache` volume on gateway (`docker-compose.yml` L207) | Per-container tmpfs or named volume per org |
| Package allowlist | Ignored when empty (L69–73) | Ignored (confirmed: arbitrary npm) |

**Recommended dev workflow:**

- Single-engineer laptop: `MCP_STDIO_IN_PROCESS=true` in `.env` — no broker required.
- Integration testing sandbox path: Linux CI job or `docker compose --profile services` with broker + DinD sidecar.

---

## Phase Breakdown

### Phase 1 — Sandbox MVP (est. 2–3 weeks)

#### 1.1 Sandbox image + agent

| Task | Path | Verification |
|------|------|--------------|
| Create sandbox Dockerfile (Node 20 + Python + uv) | `services/mcp-broker/sandbox-image/Dockerfile` | `docker build` succeeds |
| Extract sandbox-agent from stdio adapter | `services/mcp-broker/sandbox-image/agent/main.py` | Agent unit tests pass |
| Share env denylist / OAuth heuristics | Copy or `shared/mcp_stdio_common.py` | Denylist tests |
| HTTP API on agent: `POST /rpc` | `services/mcp-broker/sandbox-image/agent/routes.py` | curl initialize handshake |

#### 1.2 Broker Sandbox Controller

| Task | Path | Verification |
|------|------|--------------|
| Docker SDK lifecycle manager | `services/mcp-broker/src/sandbox/docker_manager.py` | Creates/stops container |
| Registry + idle reaper | `services/mcp-broker/src/sandbox/registry.py`, `reaper.py` | Idle container stopped after timeout |
| REST routes | `services/mcp-broker/src/sandbox/routes.py` | `pytest services/mcp-broker/tests/` |
| Internal auth middleware | `services/mcp-broker/src/auth.py` | 401 without key |
| Wire into FastAPI app | `services/mcp-broker/src/main.py` | `/health` shows docker ok |
| Broker Dockerfile + compose | `services/mcp-broker/Dockerfile`, update `docker-compose.yml` | `docker compose --profile services up` |

**docker-compose.yml changes:**

- Move `mcp-broker` out of `profiles: services` for prod-like stack OR document `--profile services` for sandbox testing.
- Mount `/var/run/docker.sock` on **mcp-broker only** (never gateway).
- Add `MCP_BROKER_INTERNAL_KEY`, `MCP_STDIO_IN_PROCESS=false` on gateway.
- Create `mcp_sandbox_bridge` network.

#### 1.3 Gateway integration

| Task | Path | Verification |
|------|------|--------------|
| `mcp_sandbox_client.py` | `gateway/ai_mesh_gateway/mcp_sandbox_client.py` | Mock broker tests |
| Branch in `send_jsonrpc` | `gateway/ai_mesh_gateway/mcp_stdio_adapter.py` L652+ | In-process tests still pass |
| Rate limit in MCP path | `gateway/ai_mesh_gateway/mcp_proxy.py` L1754+ | New test: 429 on exceed |

#### 1.4 Tests

| Layer | Path | Gate |
|-------|------|------|
| Unit (agent) | `services/mcp-broker/tests/test_agent_rpc.py` | `pytest services/mcp-broker -q` |
| Unit (broker) | `services/mcp-broker/tests/test_sandbox_lifecycle.py` | Docker mock |
| Gateway unit | `gateway/ai_mesh_gateway/tests/test_mcp_sandbox_client.py` | `cd gateway && pytest … -q` |
| Regression | `gateway/ai_mesh_gateway/tests/test_e12_mcp_security.py` | All pass with `MCP_STDIO_IN_PROCESS=true` AND broker mock |
| Integration | `gateway/ai_mesh_gateway/tests/test_mcp_sandbox_integration.py` | Requires Docker; mark `@pytest.mark.docker` |
| E2E | Extend `tests/e2e/` or MCPConnectorPanel flow | Stdio preset (Playwright) via sandbox |

**Backend gate (per AGENTS.md):** `cd gateway && python -m pytest ai_mesh_gateway/tests -q`

#### 1.5 Documentation (inline only)

- Update comment in `frontend/src/components/MCPConnectorPanel.jsx` L64–69 to note prod uses per-org sandbox (not gateway subprocess).
- Update `memory_agent_mcp_gateway.md` architecture section after implementation.

---

### Phase 2 — Hardening (est. 1–2 weeks)

| Task | Notes |
|------|-------|
| gVisor on Linux prod | `MCP_SANDBOX_RUNTIME=runsc`; CI smoke on runsc |
| Prometheus metrics | sandbox count, spawn latency, reaper evictions |
| Warm pool on org MCP server create | Hook control plane signal → broker `ensure` |
| Resource tuning per org tier | Optional `org_config.mcp_sandbox_memory_mb` |
| Remove Node from gateway image | `gateway/Dockerfile` slim down |

**Explicitly NOT Phase 2 (was in draft):** package allowlist, egress denylist — user rejected restrictions.

---

### Phase 3 — Product alignment

| Task | Notes |
|------|-------|
| MCPConnectorPanel sandbox status UI | Show container health from `GET /v1/sandbox/{org}/status` |
| Firecracker evaluation | Only if gVisor insufficient |
| HTTP MCP path to sandbox | Out of scope unless remote stdio proxy needed |

---

## Testing Strategy

### Unit

- **sandbox-agent:** JSON-RPC line protocol, OAuth hang detection (`_OAUTH_HINT_SUBSTRINGS` L84–95), env denylist, oversized line handling.
- **broker:** ensure idempotency, reaper timing (mock clock), auth rejection, org quota.
- **gateway client:** timeout mapping, broker 502 → `RuntimeError` with safe message.

### Integration (Docker required)

1. Start broker with socket mount.
2. Register stdio server in control plane (or fixture config).
3. Call `send_jsonrpc` with `MCP_STDIO_IN_PROCESS=false`.
4. Assert process runs in labeled container (`ai_mesh.org_slug` label).
5. Assert second org cannot see first org's `MCP_REMOTE_CONFIG_DIR` files.

### E2E

- Use existing `mcp-stub` pattern (`services/mcp-stub/src/main.py`) — add **stdio variant** or use minimal `@modelcontextprotocol/server-everything` via npx.
- Playwright: MCPConnectorPanel register stdio → tools/list → tools/call.
- Verify MCPEvent row in control plane.

### Security regression

- Re-run `test_e12_mcp_security.py` with broker path mocked — credential block, result redaction, key allowlist unchanged.
- Negative test: sandbox child cannot read `GATEWAY_INTERNAL_API_KEY` from env.

---

## Risks and Mitigations

| Risk | Severity | Mitigation |
|------|----------|------------|
| Cold start latency (container + npx fetch) | High | Warm `ensure`; per-org npm cache volume; keep init timeout 120s |
| Docker socket on broker = root-equivalent | Critical | Socket only on broker; auth key; never expose broker publicly; seccomp on broker container |
| macOS dev cannot test sandbox path natively | Medium | `MCP_STDIO_IN_PROCESS=true` default in `.env.example`; Linux CI for sandbox |
| gVisor syscall incompatibility with specific MCP package | Medium | Phase 1 runc; gVisor in Phase 2 with package compatibility suite |
| Tenant escape via arbitrary npm | High | Container boundary + no secrets + full scan/audit; accept egress exfil risk of tool-accessible data |
| Broker SPOF | Medium | Health checks; multiple broker replicas + leader election (future) |
| Stale sandbox after credential rotation | Medium | Sandbox-agent env-compare restart (mirror `mcp_stdio_adapter.py` L459–467) |

---

## Rollback Plan

1. Set `MCP_STDIO_IN_PROCESS=true` on gateway — immediate revert to in-process spawn (no broker dependency).
2. Stop mcp-broker; idle sandboxes remain stopped.
3. `docker rm -f $(docker ps -aq -f label=ai_mesh.role=mcp-sandbox)` cleanup script in `scripts/mcp_sandbox_cleanup.sh`.
4. No DB migrations required Phase 1 — rollback is env-flip only.

---

## Out of Scope — Phase 1

- WASM / E2B / Firecracker runtime
- Per-server container granularity (only per-org)
- npm package allowlist or egress firewall
- HTTP/SSE/WebSocket MCP transport changes
- Replacing `mcp_scan_orchestrator`
- mcp-broker as tool-policy mediation layer (distinct from sandbox)
- Frontend sandbox dashboard (Phase 3)
- Kubernetes manifests (document assumptions; compose first)
- Removing Node from gateway image

---

## File Path Summary (new / modified)

| Action | Path |
|--------|------|
| **New** | `services/mcp-broker/sandbox-image/Dockerfile` |
| **New** | `services/mcp-broker/sandbox-image/agent/` |
| **New** | `services/mcp-broker/src/sandbox/` |
| **New** | `services/mcp-broker/tests/` |
| **New** | `services/mcp-broker/Dockerfile` |
| **New** | `gateway/ai_mesh_gateway/mcp_sandbox_client.py` |
| **New** | `gateway/ai_mesh_gateway/tests/test_mcp_sandbox_client.py` |
| **Modify** | `services/mcp-broker/src/main.py` |
| **Modify** | `gateway/ai_mesh_gateway/mcp_stdio_adapter.py` |
| **Modify** | `gateway/ai_mesh_gateway/mcp_proxy.py` (rate limit) |
| **Modify** | `docker-compose.yml` |
| **Modify** | `frontend/src/components/MCPConnectorPanel.jsx` (comment only) |

---

## References (repo evidence)

| Topic | Location |
|-------|----------|
| In-process stdio spawn | `gateway/ai_mesh_gateway/mcp_stdio_adapter.py` L487–496 |
| Env secret denylist | `gateway/ai_mesh_gateway/mcp_stdio_adapter.py` L136–246 |
| MCP JSON-RPC handler | `gateway/ai_mesh_gateway/mcp_proxy.py` L1754+ |
| Adapter forward | `gateway/ai_mesh_gateway/mcp_proxy.py` L1685–1747 |
| Broker scaffold | `services/mcp-broker/src/main.py` |
| MCP_BROKER_URL | `docker-compose.yml` L187 |
| Gateway Node/npx | `gateway/Dockerfile` L28–32 |
| Rate limit helper | `gateway/ai_mesh_gateway/rate_limit_enforcement.py` |
| MCP security tests | `gateway/ai_mesh_gateway/tests/test_e12_mcp_security.py` |
| Gap analysis | `.skill-workspace/master_issues_plan.md` |
