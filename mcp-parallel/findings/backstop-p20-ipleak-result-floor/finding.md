# CHG-0074 — internal-network IP leakage in tool RESULTS bypassed the redaction floor (fail-open)

**Change-id:** CHG-0074
**Date:** 2026-07-02
**Severity:** MEDIUM–HIGH (internal / cloud-metadata IP disclosure in tool results under the DEFAULT config; a real fail-open, plus a swallowed floor-block fail-open it surfaced)
**Area:** HARDEN 1.4 — field-level redaction of tool RESULTS (byte-verified, fail-closed) + enforce-by-tag
**Files:** `gateway/ai_mesh_gateway/mcp_proxy.py`, `gateway/ai_mesh_gateway/mcp_scan_orchestrator.py` (+ `tests/test_mcp_result_ipleak_floor.py`)
**Whose work it touches:** the MCP result-enforcement floor (E12 lineage: CHG-0057) + the scan orchestrator; same 1.4 result surface as CHG-0073.

## How it was found (devil's-advocate on CHG-0073)

CHG-0073 added `internal_ipv6`/`link_local_ipv4` to the pattern layer and proved `redact_all`/`detect_ip_leakage`
mask them. The repo's CLAUDE.md mandates challenging one's own fix: *"are these regexes actually wired into
the live tool-RESULT enforcement path, or just padding patterns.py?"* Tracing the real path
(`_scan_tool_result_floor` → `_mcp_security_scan` → `scan_mcp_payload`) exposed that they were **not fully wired**.

## Gap 1 — IP-leakage detected + tagged but NOT redacted under the default `tag` posture

The MCP orchestrator (`mcp_scan_orchestrator._scan_text_tier1`) detects IP leakage (line ~319) and tags it
`INFRA`, but only **redacts under `enforcement=="redact"`** / blocks under a block posture. Under the DEFAULT
`tag` (and `flag`/`monitor`) posture it returns the result **unmutated**. The mcp_proxy **E12 result-redaction
floor** that upgrades `tag`→`redact` for sensitive results was gated on `_findings_have_secret_or_pii(findings)`
— which matches only `threat_type in ("pii","secret")` and **excludes the entire `ip_leakage` class**. Net:
an internal / cloud-metadata IP in a tool RESULT was detected + tagged but **egressed RAW** — asymmetric with
PII/secret, which get the floor.

**Proven end-to-end** at the real entrypoint (`_scan_tool_result_floor`, default action `tag`,
`_mcp_redact_result_on_detect_enabled()==True`):

| result content | before |
|---|---|
| `alice.smith@corp.example` (PII) | floored → masked (safe) |
| `sk-ant-…` (secret) | floored → masked (safe) |
| `169.254.169.254` (IMDS) | **raw LEAK** |
| `fc00::1234:5678` (internal IPv6) | **raw LEAK** |
| `10.10.5.7` (RFC1918, PRE-EXISTING) | **raw LEAK** |

The RFC1918 row shows this pre-dates CHG-0073 — the *entire* IP-leakage class bypassed the result floor.

## Gap 2 — swallowed floor-block (fail-open on unmaskable mixed results)

The floor re-scans with `enforcement_override="redact"`. When a detected value `redact_all` cannot mask
survives (a private FILE PATH — deliberately flag-tier — alongside the IP/PII), the orchestrator's egress-byte
verify blocks. But all three floor sites only swapped in masked content `if floor_content is not target` and
**ignored the re-scan's `blocked` flag**, so the block was swallowed and the RAW result forwarded (the same
function's exception handler already blocks fail-closed — an inconsistency). This also affected PII+file-path.

## Fix

1. `McpFinding` gains `matched_kinds: list[str]` (+ in `to_finding_dict`); the pii/secret/ip_leak finding
   passes `matched_kinds=kinds`. Lets the floor distinguish an ENFORCEABLE network key from a flag-tier file
   path structurally (no `detail` parsing).
2. `mcp_proxy._findings_have_infra_network_leak(findings)` — True iff an `ip_leakage` finding's `matched_kinds`
   intersect `_INFRA_NETWORK_KEYS` (the keys `redact_all` masks: internal_ipv4/ipv6, link_local_ipv4,
   internal_hostname, internal_url). **Scoped to network keys** so a file-path-only result never triggers the
   floor (→ never force-blocks a benign code/file tool result).
3. OR the helper into all **three** floor triggers (`_scan_tool_result_floor` + the two org-path sites).
4. **Propagate the floor-block fail-closed** at all three sites: if the redact re-scan blocked (unmaskable
   survivor), withhold the result (`[BLOCKED]` / `result_redaction_floor_block`) instead of forwarding raw.

## Behaviour after fix (byte-level, real floor)

| result | after |
|---|---|
| metadata / IPv6 / RFC1918 / CGNAT / internal-hostname | **floored → masked** |
| PII / secret | floored → masked (unchanged) |
| file-path ONLY | **stays raw (flag-tier), NOT blocked** (no regression) |
| network-IP + file-path, or PII + file-path | **BLOCKED (fail-closed)** |

## Verify

```
cd gateway
.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_result_ipleak_floor.py -q   # 11 passed
.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                    # 1316 passed, 0 failed
```
Broker (regression, skip pre-existing ws hangs): `services/mcp-broker && .venv/bin/python -m pytest tests -q -k "not websocket"` → 108 passed.

## Residual / follow-ups
- File paths remain flag-tier (deliberate FP trade-off; documented). A file-path-only result still egresses
  raw+flagged — masking file paths is a separate, higher-FP decision.
- The `ext_mcp_proxy` path uses `_scan_tool_result_floor` (fixed here); its own result-scan floor now covers IP.
