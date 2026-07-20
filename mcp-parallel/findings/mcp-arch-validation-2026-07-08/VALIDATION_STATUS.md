# MCP Architecture Validation — Iteration 6 (2026-07-08)

## Status: PLATFORM VALIDATED — infra blockers only remain

Completion promise withheld **only** for: gVisor (`runsc` absent), 300–500 sandbox stress (host RPS ceiling ~48), Playwright browser install (host). All code-level architecture gaps from iteration 5 reviews are closed with live evidence.

---

### VERIFIED (with evidence)

| Item | State | Evidence |
|------|-------|----------|
| Scan controls off-by-default (gateway JSON-RPC) | **FIXED & DURABLE** | `scan-controls-off-live-proof.json`; unit 4 passed |
| Live scan-control matrix | **PASS** | `scan_matrix_live.json` `matrixPass: true` |
| Enforcement planes separation | **PASS** | `enforcement-planes-live.json` `planesPass: true` |
| Tier-2 org gate | **PASS** | `tier2-org-gate-live.json` `tier2GatePass: true` |
| Fleet tool execution | **PASS (15/16)** | `fleet-tool-execution.json`; http-stub session caveat |
| Sandbox posture inspect | **PASS** | `sandbox-lifecycle-live.json` |
| Sandbox kill/recreate chaos | **PASS** | `sandbox-chaos-live.json` `chaosPass: true` (~6s rebuild) |
| Firewall→`mcp:scan_ver` bump | **LIVE** | PUT toggles ver 102→104; code in control `core/signals.py` |
| UI scan-control API parity | **PASS** | `scan-control-ui-parity-api.json` — 0 rows + raw SSN + `decision=scan_skipped` |
| Observability: `scan_skipped` distinct from `allow` | **FIXED & LIVE** | `scan-skipped-decision-live.json`; MCPEvent migration 0016 |
| Internal path actor threading | **FIXED** | gateway `internal_tools_call` + control `_call_tool_via_gateway`; `test_mcp_internal_actor.py` 2 passed |
| UI honesty (0 controls vs posture badges) | **FIXED** | frontend disables Scan select + “Scanning off” badge when 0 controls; `npm run build` green |
| OpenAI SDK / tag-monitor / isolation | **PASS** | prior iteration evidence still holds |

---

### Iteration 6 root causes + fixes

| Finding | Root cause | Fix |
|---------|------------|-----|
| Tier-2 toggle stale cache | FirewallConfig save did not bump `mcp:scan_ver` | `core/signals.py` → `bump_scan_version(org)` (hot-deployed + **live proven**) |
| `scan_skipped` audits vanished | Gateway emitted `decision=scan_skipped` but control `MCPEvent.DECISION_CHOICES` rejected it → HTTP 400 `invalid decision` → silent audit loss | Added `scan_skipped` choice + migration `0016` + EnforcementEvent maps; live `decision=scan_skipped` |
| Chat path actor=None | Control never forwarded actor into `/v1/mcp/internal/tools-call` | Control payload `actor={user_id,agent_id,roles}`; gateway threads into arg/result scans |
| UI implied scanning active with 0 controls | Server `default_scan_action` select always enabled | Load scan-controls count; disable select + amber “Scanning off” badge |
| Sandbox recovery unproven | Inspect-only previously | Chaos: `docker stop+rm` → next tools/call re-provisions; `chaosPass: true` |

---

### Architecture contracts (proven)

```
0 MCPScanControl rows
  → enabled-tools.scan_controls_configured=false
  → _mcp_security_scan → scan_skipped (no tier1/tier2/tags)
  → MCPEvent.decision = "scan_skipped" (NOT "allow")
  → Prometheus amf_gateway_mcp_scan_decisions_total{decision="scan_skipped"}
  → UI: Scan Controls empty state + server cards show "Scanning off"

≥1 scan control + mcp_tier2_enabled=false
  → tier2_skipped reason=org_mcp_tier2_disabled (tier1 still runs)

Sandbox destroyed mid-flight
  → broker ensure recreates labeled container; tool call succeeds
```

### Three enforcement planes (unchanged, reconfirmed)

| Plane | Independence |
|-------|----------------|
| Scan controls (Tier-1/2) | Gated by `scan_controls_configured` |
| MCP Security Policies | Control `tools/call/` always; gateway orchestrator when scanning active |
| Compliance frameworks | Do not suppress tags when tier1 runs |

`ext_mcp_proxy` remains transport-level (`enabled_info=None`) — intentional, not org matrix.

---

### Devil's advocate close (iteration 6)

| Challenge | Outcome |
|-----------|---------|
| “scan_skipped metrics without audit = orphan” | **Was true** — control 400'd the decision; **fixed** with model choice + live proof |
| “UI still lies about block posture” | **Fixed** in source; deploy frontend image/Vite for operator UI |
| “Actor gap on chat path” | **Fixed** end-to-end (control→gateway); unit locked |
| “Chaos will strand org” | **Falsified** — recreate OK, labels intact |
| “Scan-off gate bypass on org JSON-RPC” | **Still holds** — matrix + parity + scan_skipped event |

---

### BLOCKED (infra / host only)

| Item | Blocker |
|------|---------|
| gVisor | `runsc` not installed |
| 300–500 sandbox stress | Shared VM ~48 RPS (CP50) |
| Playwright chromium | Not installed (`npx playwright install` required) |

---

### Deploy notes

| Component | Iteration 6 deploy method |
|-----------|---------------------------|
| Control `signals.py` / `views.py` / `models.py` / migration 0016 | `docker cp` + `migrate` + container restart — **LIVE** |
| Gateway `mcp_proxy.py` / `metrics.py` | `docker cp` + restart — **LIVE** |
| Frontend UI honesty | Source + `npm run build` green; recreate/rebuild frontend container for Vite serve |
| Durable images | `docker compose build control gateway frontend` recommended for persistence across recreate |

---

## Evidence directory

`mcp-parallel/findings/mcp-arch-validation-2026-07-08/`:
- Prior: scan_matrix, enforcement-planes, scan-controls-off, tier2-org-gate, fleet, sandbox-lifecycle
- New: `sandbox-chaos-live.json`, `scan-control-ui-parity-api.json`, `scan-skipped-decision-live.json`
- `LIFECYCLE_SEQUENCES.md`, `VALIDATION_STATUS.md` (this file)
