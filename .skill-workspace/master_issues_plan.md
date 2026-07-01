# Master Issues Plan — MCP Gateway Gap Analysis

**Last updated:** 2026-06-29 — architecture decisions confirmed; see `.skill-workspace/implementation_plan.md`

## Executive Summary (Top 5)

1. [CRITICAL] No per-tenant sandbox — npm executes in shared gateway container → **Fix: per-org Docker sandbox via mcp-broker**
2. [CRITICAL] Sandbox Controller missing — **Fix: extend `services/mcp-broker` (not a new service)**
3. [HIGH] MCP JSON-RPC path has no rate limiting → **Fix: wire `enforce_org_tpm_rate_limit` + burst/RPM in `org_mcp_jsonrpc`**
4. [HIGH] Subprocess isolation is env-only → **Fix: container cgroup + no gateway secrets in sandbox**
5. [MEDIUM] mcp-broker scaffold unused → **Fix: implement Sandbox Controller API behind existing `MCP_BROKER_URL`**

---

## Confirmed Architecture Decisions (2026-06-29)

| Decision | Choice |
|----------|--------|
| Sandbox granularity | **One container per org (tenant)** |
| Dev mode | **`MCP_STDIO_IN_PROCESS=true`** keeps in-gateway stdio spawn |
| npm packages | **No restrictions** — arbitrary packages allowed |
| Controller service | **Extend `services/mcp-broker`** as Sandbox Controller |
| Network egress | **Full outbound** for MCP tool traffic |
| Phase 1 runtime | **Docker runc** + cgroup limits |
| Phase 2 hardening | **gVisor (`runsc`)** on Linux prod |
| Firecracker | Deferred unless VM-grade isolation required |

---

## Issue: GAP-001 — No Per-Tenant Sandbox Isolation

**Severity:** Critical  
**Confidence:** High  
**Evidence:**
- `gateway/ai_mesh_gateway/mcp_stdio_adapter.py` L487–496: `asyncio.create_subprocess_exec` in gateway
- `gateway/Dockerfile` L28–32: npx embedded in gateway image
- `docker-compose.yml` L195–208: on-demand npx fetch in gateway container

**Root cause:** DECISION-D removed ContextForge/Enkrypt; gateway became sole exec layer without container per tenant.

**Recommended fix:** One Docker sandbox per org; sandbox-agent manages stdio MCP processes inside container; gateway delegates via mcp-broker when `MCP_STDIO_IN_PROCESS=false`.

**Blast radius:** All stdio MCP customers; security boundary of entire product.

**Status:** Planned — Phase 1 in `implementation_plan.md`

---

## Issue: GAP-002 — Sandbox Controller Missing

**Severity:** Critical  
**Confidence:** High  
**Evidence:** `services/mcp-broker/src/main.py` — health endpoint only; no sandbox API. `docker-compose.yml` L187 sets `MCP_BROKER_URL` on gateway but unused.

**Recommended fix:** Extend mcp-broker with `POST /v1/sandbox/{org}/ensure`, `POST /v1/sandbox/{org}/stdio/rpc`, `DELETE /v1/sandbox/{org}`; broker owns Docker socket (not gateway).

**Blast radius:** mcp-broker service + gateway adapter change.

**Status:** Planned — Phase 1 in `implementation_plan.md`

---

## Issue: GAP-003 — MCP Routes Unrate-Limited

**Severity:** High  
**Confidence:** High  
**Evidence:** `rate_limit_enforcement.py` used from `main.py` chat paths; `mcp_proxy.org_mcp_jsonrpc` (L1754+) has no `enforce_org_tpm_rate_limit` calls.

**Recommended fix:** Add rate limit gate at `org_mcp_jsonrpc` entry (reuse `enforce_org_tpm_rate_limit` + `enforce_org_burst_rpm`).

**Blast radius:** `mcp_proxy.py` only; low regression risk.

**Status:** Planned — parallel track Phase 1

---

## Issue: GAP-004 — Weak Execution Isolation (Env-Only Sandboxing)

**Severity:** High  
**Confidence:** High  
**Evidence:** `mcp_stdio_adapter.py` L226–246 env allowlist; no cgroup/seccomp at L489–496.

**Recommended fix:** Per-org Docker container with memory/CPU/pids limits; `_SECRET_ENV_DENYLIST` enforced in sandbox-agent; no gateway volume mounts. Phase 2: gVisor runtime.

**Blast radius:** stdio transport only.

**Status:** Planned — Phase 1 (runc), Phase 2 (gVisor)

---

## Issue: GAP-005 — mcp-firewall / Secure-MCP-Gateway Legacy Gap

**Severity:** Medium  
**Confidence:** High  
**Evidence:** `control/.../mcp_firewall_client.py` L1–10 documents CLI-only external tool; preflight/postflight are no-ops.

**Recommended fix:** Document built-in `mcp_scan_orchestrator` as canonical; dead code cleanup optional.

**Blast radius:** Documentation + dead code cleanup.

**Status:** Unchanged — independent of sandbox work

---

## Issue: GAP-006 — Package Allowlist / Egress Policy (REMOVED)

**Severity:** N/A  
**Status:** **Won't fix per user decision** — arbitrary npm packages and full outbound network are intentional. Do not implement `MCP_STDIO_PACKAGE_ALLOWLIST` enforcement in sandbox path.

---

## Dependency Map

```
GAP-002 blocks GAP-001 → GAP-004
GAP-003 independent (can ship in parallel)
GAP-005 independent (cleanup)
GAP-006 removed from scope
```

## Implementation Reference

Full plan: **`.skill-workspace/implementation_plan.md`**
