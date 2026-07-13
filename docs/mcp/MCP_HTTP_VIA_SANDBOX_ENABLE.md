# Enable `MCP_HTTP_VIA_SANDBOX` (Cursor-owned, no gateway source edits)

Streamable-http and SSE route through the per-org sandbox when the gateway process has
`MCP_HTTP_VIA_SANDBOX=true` **and** the running gateway image includes §3 wiring
(`_is_sandbox_routed` + `broker_send_rpc` in `mcp_proxy.py`).

## Quick enable (live stack)

```bash
# 1. Ensure the zeroshield sandbox network exists, then start transport stubs
bash scripts/ensure_org_sandbox_network.sh
docker compose --profile transport-stubs up -d

# (Legacy alternative — same stubs, manual docker run)
# bash scripts/mcp_transport_stubs_up.sh

# 2. Write .env + recreate control/gateway (also sets MCP_ALLOW_INTERNAL_HOSTS for stub hostnames)
bash scripts/mcp_enable_http_via_sandbox.sh

# 3. Register http/sse servers + write manifest (websocket still Claude-owned)
python3 scripts/mcp_register_transport_servers.py

# 4. Verify wiring + transports (needs deployed §3 code — see below)
TRANSPORT_MANIFEST=mcp-parallel/findings/p4-13/TRANSPORT_MANIFEST.zeroshield.json \
  ROUNDS=3 python3 scripts/mcp_sandbox_transport_verify.py
```

## Env keys (`.env`, gitignored)

| Key | Purpose |
|-----|---------|
| `MCP_HTTP_VIA_SANDBOX=true` | Gateway opts into sandbox routing for http/sse |
| `MCP_ALLOW_INTERNAL_HOSTS` | Control SSRF allowlist for `http-everything.stub:3001`, `sse-everything.stub:3002`, … |

Stub hostnames use a **dotted** suffix (`.stub`) so Django `URLField` validation passes; Docker
network aliases on `mcp_sandbox_net_zeroshield` resolve them from sandboxes.

## Deployed vs committed (critical)

The wiring gate in `scripts/mcp_sandbox_transport_verify.py` reads **git source**. The live
gateway container may still be a **baked image** without §3:

```bash
grep -c broker_send_rpc gateway/ai_mesh_gateway/mcp_proxy.py          # source → expect 2
docker exec $(docker ps -qf name=gateway) \
  grep -c broker_send_rpc /app/gateway/ai_mesh_gateway/mcp_proxy.py   # runtime → must match
```

If runtime is `0`, http/sse calls use the legacy backend path (empty tools when sync failed).
**Claude-owned:** rebuild/redeploy gateway or hot-deploy `mcp_proxy.py` before P4.13 `[x]`.

## Compliance frameworks vs detection tags (trace)

These are **different concepts** on the MCP page and in audit telemetry:

| Concept | Where configured | What it means |
|---------|------------------|---------------|
| **Compliance frameworks** | Firewall config → Audit & Compliance (`compliance_frameworks`) | Operator-selected reporting scope (SOC2, HIPAA, GDPR, …). Empty = no framework filter applied to config — **detection tags still appear** on MCP events from scans. |
| **Detection / compliance tags** | Emitted by gateway scan (`patterns.py` → normalized at audit write) | Per-event labels from what was detected (e.g. PII, SECRET, GDPR-PII). Shown on MCP Observability events and simulator verdicts regardless of `compliance_frameworks`. |

Pipeline trace stages may include `matched_policies`, `compliance_tags`, and `decision_source` on each stage — see `docs/pipeline/CANONICAL.md` for the chat path; MCP tool results use the same tag vocabulary at the MCPEvent audit boundary (`CHG-0059`).

## Remaining Claude-owned gaps

1. **Websocket:** `mcp_ws_adapter` in-gateway (not `broker_send_rpc`); no ws stub in Cursor scripts.
2. **Default ON:** flip compose default after e2e proof (optional).
3. **Gateway image:** bake §3 into `gateway/Dockerfile` rebuild.
