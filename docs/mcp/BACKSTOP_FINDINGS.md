# MCP Hardening — BACKSTOP Findings (G1 item 1)

**Date:** 2026-07-02 · **Changelog:** CHG-0002 · **Method:** read-only 6-auditor parallel workflow
(`wf_ca0d6349-33e`, 7 agents, 904k tokens) over the gateway guardrail chain, control-plane
compliance/authz, broker sandbox, stress harnesses, and frontend — synthesized/deduped to 24 findings
(13 high · 8 medium · 1 low). Every finding is grounded in code (file:line + a `Verify` command).

**Independently spot-verified by the backstop (not just agent-claimed):**
- SSE `ext_mcp_proxy` egress passthrough — confirmed `text/event-stream` → `streaming_egress_unscanned`
  → `aiter_bytes()` verbatim (`mcp_proxy.py:1021-1035`).
- Dead cross-tenant oracle — confirmed `mcp_scale_matrix_live.py:129` iterates `for fs in []`, so
  `foreign_org_events` is structurally always 0 (`python3 -c "any(1 for fs in [])"` → False). Intended
  code was `for fs in other_slugs`.
- 300–500 sandbox ceiling — confirmed `mcp_scale_provision.py:29-33` hardcodes a 3-org literal;
  `SERVERS_PER_ORG` defaults 5 → hard max 15 sandboxes.

> Overall verdict: the dominant signal is a wall of **omissions / fail-open parity gaps**, not a
> confirmed cross-session regression. The core G2 1.4 deliverables (fail-closed result redaction,
> per-actor tool authz, tag-driven enforcement), the G3 isolation controls (gVisor-required +
> network-level egress deny + all-transports-in-sandbox), the G5 VERY-HARD stress suite (14–20), and the
> G6 frontend redaction surfaces (21) are all still unmet at the required bar. Several items previously
> marked done overstate: "scale matrix validated" was 6/6 403s at 15 MCPs, and the isolation metric that
> backed it is a dead no-op.

---

## Summary

Six subsystem auditors reviewed the MCP-hardening effort across the gateway guardrail chain (mcp_proxy.py, mcp_scan_orchestrator.py, patterns.py, policy_engine.py), the control-plane compliance/authorization path (mcp_connector), the broker sandbox architecture, and the stress-harness + frontend surfaces. After deduplication (37 raw rows → 24 findings; 3 pure `ok`/`info` verification rows dropped) the dominant signal is a wall of OMISSIONS and fail-open parity gaps rather than any confirmed cross-session regression. The load-bearing claims were re-verified against code: the SSE branch of `ext_mcp_proxy` logs `streaming_egress_unscanned` and forwards `aiter_bytes()` verbatim; the scale-matrix cross-tenant oracle iterates `for fs in []` so `foreign_org_events` is structurally always 0 (a fabricated "isolation proven" metric); the scale provisioner's `ORGS` is a hardcoded 3-element literal (15-sandbox ceiling); the gateway tag vocabulary (PII/HIPAA/GDPR/PCI-DSS/PHI/SECRET/INFRA/SOC2) is disjoint from the `ComplianceTag` catalog (FERPA/GDPR-PII/HIPAA-PHI/ITAR/PCI-CARD); and `PolicyManagementPanel.jsx:399` contains a literal `Â·`. The core G2 "1.4 Context Assembly & MCP Guardrails" deliverables (result redaction fail-closed, per-actor tool authz, compliance-tag enforcement), the G3 isolation controls (gVisor + network-level egress deny), the G5 VERY-HARD stress suite (14-20), and the G6 frontend redaction surfaces (21) are all still unmet at the required bar. Two verification rows passed with only minor optional gaps (sandbox resource limits correct except no disk quota on the rw auth volume; reaper/restart-safety correct except stopped-orphan GC) and were dropped as non-issues.

## Ranked findings

### HIGH — SSE (Streamable-HTTP) tool RESULTS egress raw and unscanned on the external transparent proxy
- Category: gap
- File: gateway/ai_mesh_gateway/mcp_proxy.py:1021
- Evidence: When the upstream content-type is `text/event-stream`, `ext_mcp_proxy` logs `ext_mcp_proxy.streaming_egress_unscanned` (:1029) and returns a `StreamingResponse` over `async for chunk in resp.aiter_bytes(): yield chunk` (:1035) — no `_scan_tool_result_floor`/`_mcp_security_scan` runs. The non-streaming JSON branch below (:1080-1118) DOES scan/redact/block, and `internal_tools_call` buffers+scans SSE (:1514-1533), so this is a direct streaming-vs-non-streaming enforcement-parity gap. SSE is the DEFAULT MCP Streamable-HTTP result mode, so any PII/secret in a tool result egresses raw with only a warning log (no verdict). The repo's own `test_ext_streaming_egress_unscanned_but_flagged` asserts exactly this.
- Maps-to: G2 item 2 (field-level redaction of RESULTS, byte-verified, fail-closed)
- Verify: `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py::test_ext_streaming_egress_unscanned_but_flagged -q`; independent oracle: feed `b'data: {"result":"ssn 123-45-6789"}\n\n'` through the stream generator and run `aidefence_has_pii` over the yielded bytes.
- Recommendation: Buffer-and-scan SSE (parse each `data:` frame, run the result floor on `result.content`, re-emit masked) like `internal_tools_call`, or fail-closed (block) SSE tool results for sensitive-data tools. A logged verdict without masked bytes is not redaction.
- Corroboration: 2 auditors. **Backstop-verified.**

### HIGH — Per-actor controls (tool authorization + field-level RBAC redaction) are not enforced on the stdio/websocket adapter path
- Category: gap / omission
- File: gateway/ai_mesh_gateway/mcp_proxy.py:2196
- Evidence: The adapter branch (2189-2322) calls the server via `_adapter_forward` and runs ONLY the generic two-tier `_mcp_security_scan(..., actor=mcp_actor)`. The gateway comment at 2196-2198 admits "G8 per-user/role field-level policy filtering (MCPToolCallView's RBAC field masks) still only fires on the backend HTTP path." The real per-actor field redaction (`apply_field_redaction`/`redact_structured` + allowlist query, control views.py:1113/1159-1193) and per-actor policy authz (`allowed_user_ids/allowed_agent_ids/allowed_roles`, views.py:1167-1198) live in `MCPToolCallView`, which stdio/ws bypass entirely. `_get_enabled_tools` keys its cache on `f"{org_slug}/{server_slug}"` (:308) with a TODO that "tool enable/disable is server-scoped (not actor-scoped)"; `actor` is threaded only for scan attribution and is never used for an access decision. The `MCPGatewayEnabledToolsView` payload (control views.py:2085-2149) carries no actor dimension. Net: "only role=admin may call tool X" is enforced for HTTP but silently ignored for stdio/websocket agents, and per-actor field masking is absent there.
- Maps-to: G2 item 3 (per-user/agent/role tool authorization) + item 2 (field-level result redaction)
- Verify: `grep -n "field-level policy filtering (MCPToolCallView" gateway/ai_mesh_gateway/mcp_proxy.py`; `grep -n 'apply_field_redaction\|redact_structured' gateway/ai_mesh_gateway/mcp_proxy.py` → none; `grep -n 'actor' gateway/ai_mesh_gateway/mcp_proxy.py | grep -iE 'allow|deny|authz|role'` → no access decision.
- Recommendation: Route stdio/ws tool calls through the same actor-scoped policy evaluation, or extend the enabled-tools payload with per-actor authz (allowed_user_ids/agent_ids/roles per tool) + apply the shared field-redaction module on the adapter path (widen the `_enabled_tools_cache` key per the mcp_proxy.py:301-305 TODO).
- Corroboration: 3 auditors.

### HIGH — Bare REST route `org_mcp_tool_call` bypasses per-key tool allowlist and per-turn call cap that the JSON-RPC route enforces
- Category: gap
- File: gateway/ai_mesh_gateway/mcp_proxy.py:2589
- Evidence: `org_mcp_jsonrpc` enforces `_tool_allowed_by_key` (:2018), the `mcp_max_tool_calls` cap via `_incr_tool_call_count`/`_tool_call_cap_exceeded` (:2044-2080), and `_is_tool_disabled` (:2086) before forwarding. `org_mcp_tool_call` (:2589-2732) performs ONLY `_validate_org_scope` + the arg/result scan — none of the three authz gates appear. `mcp_allowed_tools`/`mcp_max_tool_calls` are gateway-only controls from the Redis auth payload (middleware.py:103-104) and are NOT forwarded to the backend (`_backend_proxy_headers` sends only user-id/prefix/roles, :1681-1693), so the backend cannot re-enforce them. A caller hitting `POST /{org}/mcp/{server}/tools/call` can invoke a tool outside its key allowlist and exceed the per-turn cap.
- Maps-to: G2 item 3 (per-key tool authorization) + item 6 (e2e chain parity)
- Verify: `grep -n "_tool_allowed_by_key\|_incr_tool_call_count\|_is_tool_disabled" gateway/ai_mesh_gateway/mcp_proxy.py` → all only inside `org_mcp_jsonrpc`, none in `org_mcp_tool_call`.
- Recommendation: Factor allowlist/cap/disable into a shared gate helper and invoke it from `org_mcp_tool_call` (and the internal/external proxy routes) before forwarding.

### HIGH — No compliance-tag-driven enforcement anywhere — tags are audit-only; only SECRET drives a control and even that is bypassable via the alternate vocabulary
- Category: omission / incomplete
- File: control/ai_mesh_control/mcp_connector/scan_controls.py:139
- Evidence: The scan-control matrix (`MCPScanControl` tier/direction/scope_type/target_mode/key_path/action, models.py:327-424; `resolve_effective_controls`) has NO compliance-tag dimension; the gateway block/redact/monitor decision derives purely from `tier1_action`/`tier2_action` + scanner `blocked` flags (mcp_scan_orchestrator.py:385-451), with `compliance_tags` computed only AFTER a finding for annotation. No branch reads a tag to decide an action, and there is no FK from Policy or `MCPScanControl` to the `ComplianceTag` model (the catalog is decorative). The only tag that drives a control is SECRET (`_findings_have_credential` returns True on `'SECRET' in tags`, patterns.py:567); HIPAA/PHI/PCI-DSS/GDPR tags trigger no tag-specific enforcement. Because two tag vocabularies coexist, a credential detected only via a policy preset is tagged `SOC2-CONF` (not `SECRET`), so the credential force-block does not fire.
- Maps-to: G2 item 5 (compliance tagging PII/IP/regulated; enforce by tag; audit)
- Verify: `grep -rnE 'if .*compliance_tag|compliance_tag[s]? *==|in compliance_tag' gateway/ai_mesh_gateway control/ai_mesh_control --include=*.py | grep -v test` → assignments only, no enforcement branch; `grep -n '"SECRET" in tags' gateway/ai_mesh_gateway/patterns.py` (:567).
- Recommendation: Add a tag-driven enforcement dimension (allow/deny list of `ComplianceTag` codes on `MCPScanControl`/Policy mapping a detected tag to block/redact), surface it in the enabled-tools payload so the gateway can gate on it, and normalize SOC2-CONF→SECRET so credential blocking is detector-origin-independent.
- Corroboration: 2 auditors.

### HIGH — Compliance-tag vocabulary is fragmented end-to-end — gateway emits non-catalog codes, ITAR/FERPA are unreachable, INFRA is uncataloged, and `MCPEvent.compliance_tags` joins zero catalog rows
- Category: mistake / gap
- File: control/ai_mesh_control/mcp_connector/models.py:307
- Evidence: `models.py:306-307` documents `compliance_tags` as a list of `ComplianceTag.code` values (`["GDPR-PII","PCI-CARD"]`). But the gateway data plane populates the same field via `_record_gateway_event(compliance_tags=_out_tags/_inbound_tags)` whose values come from `patterns.COMPLIANCE_TAG_MAP` and emit PII/HIPAA/GDPR/PCI-DSS/PHI/SECRET/INFRA/SOC2 — none of which equal a catalog code. (Verified: gateway set = {GDPR,HIPAA,INFRA,PCI-DSS,PHI,PII,SECRET,SOC2}; catalog = {FERPA,GDPR-PII,HIPAA-PHI,ITAR,PCI-CARD} — disjoint.) The control-plane direct path emits the hyphenated catalog codes instead, so one column carries two disjoint vocabularies and the dominant (gateway) one joins to zero catalog rows. Additionally, catalog codes ITAR and FERPA can never be emitted by either MCP producer, and gateway `INFRA` (internal IPs/hostnames/paths) has no catalog row at all.
- Maps-to: G2 item 5
- Verify: `grep -oE '\["[A-Z][A-Z0-9-]*"' gateway/ai_mesh_gateway/patterns.py | tr -d '["' | sort -u` vs `grep -oE '"[A-Z-]+"' control/ai_mesh_control/policy/compliance_tags.py | tr -d '"' | sort -u`.
- Recommendation: Normalize both producers onto a single vocabulary keyed on `ComplianceTag.code` (alias PCI-DSS→PCI-CARD, GDPR/PII→GDPR-PII, HIPAA/PHI→HIPAA-PHI, SECRET→SOC2-CONF), add reachable detectors/preset mappings for ITAR/FERPA (or remove them from the catalog), add an IP/INFRA catalog row, and fix/drop the models.py docstring until unified.
- Corroboration: 2 auditors.

### HIGH — Only the stdio transport reaches the per-org sandbox; http/sse/websocket still execute in the gateway/control backend
- Category: incomplete
- File: services/mcp-broker/src/sandbox/routes.py:212
- Evidence: The broker exposes exactly ONE transport RPC route — `@router.post("/{org_slug}/stdio/rpc")` (:212) — no http/sse/ws broker route exists. The gateway's `broker_send_jsonrpc` POSTs only to `/v1/sandbox/{org}/stdio/rpc` (mcp_sandbox_client.py:175), and only the stdio adapter delegates to it (and only when `MCP_STDIO_IN_PROCESS=false`, mcp_stdio_adapter.py:684-701). `mcp_ws_adapter.send_jsonrpc` has NO broker path — it always opens the socket in-gateway via `websockets.client.connect` (:135). streamable-http/sse route to the control plane `POST {_BACKEND_URL}/api/mcp-connector/tools/call/` (mcp_proxy.py:2326) or direct httpx to the upstream (:1249-1279). The sandbox agent implements all four transports (`send_upstream_jsonrpc`), but nothing in the data plane ever forwards http/sse/ws to it — that code is unreachable.
- Maps-to: G3 item 7
- Verify: `grep -n "stdio/rpc\|broker_send_jsonrpc\|websockets.client.connect\|api/mcp-connector/tools/call" gateway/ai_mesh_gateway/mcp_sandbox_client.py gateway/ai_mesh_gateway/mcp_ws_adapter.py gateway/ai_mesh_gateway/mcp_proxy.py services/mcp-broker/src/sandbox/routes.py`.
- Recommendation: Add broker RPC routing for streamable-http/sse/websocket and switch `mcp_ws_adapter` + the http/sse tools-call paths to delegate to the broker the same way stdio does. Until then, do not claim "all transports run in the per-org sandbox."

### HIGH — gVisor/runsc runtime is opt-in and unset by default; the shipped deployment runs sandboxes on runc (shared kernel) with no custom seccomp — fail-open
- Category: gap / incomplete
- File: services/mcp-broker/src/sandbox/docker_manager.py:343
- Evidence: `SandboxDockerConfig` defaults `runtime=None, runtime_required=False` (:80-86). `_resolve_runtime` returns `None` (Docker uses default runc) unless `runtime_required` is true (:343-359); fail-closed only fires when `MCP_SANDBOX_RUNTIME_REQUIRED=true`. `_run_kwargs` sets cap_drop:ALL + no-new-privileges (:438-439, good) but NO custom seccomp/AppArmor profile. The one compose that defines the broker (docker-compose.yml:235-240) sets neither `MCP_SANDBOX_RUNTIME` nor `MCP_SANDBOX_RUNTIME_REQUIRED`, and docker-compose.prod.yml defines no mcp-broker at all. So by default containers run under runc (shared kernel), silently degrading instead of failing closed.
- Maps-to: G3 item 12
- Verify: `cd services/mcp-broker && ./.venv/bin/python -m pytest tests/test_sandbox_lifecycle.py -q -k 'run_kwargs or runtime'`; `grep -n 'MCP_SANDBOX_RUNTIME\|EGRESS' docker-compose.yml docker-compose.prod.yml`; live `docker inspect <sandbox> --format '{{.HostConfig.Runtime}}'` → `runc`.
- Recommendation: Set `MCP_SANDBOX_RUNTIME=runsc` + `MCP_SANDBOX_RUNTIME_REQUIRED=true` on the broker in both compose files (define the broker in prod compose), default `runtime_required=true` in prod images, and add a pinned custom seccomp profile; prove it live (`Runtime=runsc`).
- Corroboration: 2 auditors. (Note: the CHG-0001 item-0 audit confirmed the enforcement *mechanism* is correct & fail-closed; this finding is that the *env is not set* in shipped compose, so the mechanism never engages.)

### HIGH — Egress lockdown is default-OFF and, even when enabled, is only a cooperative HTTP(S)_PROXY env var — no network-level default-deny; raw-socket exfil/C2 is uncontained
- Category: gap / incomplete
- File: services/mcp-broker/src/sandbox/docker_manager.py:361
- Evidence: `egress_lockdown` defaults False (:83) and `from_env` enables it only if `MCP_SANDBOX_EGRESS_LOCKDOWN` is truthy (:103). `_egress_proxy_env` (:361-370) only injects `HTTP_PROXY/HTTPS_PROXY/NO_PROXY` into the child — env proxies are honored only by cooperating HTTP clients; a malicious npx/uvx child can open a raw TCP socket to any host and ignore them. `ensure_org_network` creates the per-org network with `driver="bridge"` and NO `internal=True` (:387-391), so every sandbox has full outbound internet via NAT; there is no iptables/network-policy egress firewall. So even with lockdown "on," a package can exfiltrate a legitimately-injected token (T3 residual). ARCHITECTURE_AND_THREATS.md §2 lists "egress allowlist" as Confirmed ABSENT.
- Maps-to: G3 item 12
- Verify: `grep -n 'egress_lockdown\|internal=\|driver="bridge"\|HTTP_PROXY\|_egress_proxy_env' services/mcp-broker/src/sandbox/docker_manager.py`; live `docker network inspect mcp_sandbox_net_<org> --format '{{.Internal}}'` → false.
- Recommendation: Enforce egress at the network layer — create the per-org network with `internal=True` and route allowed egress through a broker-controlled proxy interface, or apply per-container iptables/eBPF egress allowlists (exact-host match: npm/PyPI + declared remote-MCP hosts only). Make it default-ON in prod.
- Corroboration: 3 auditors.

### HIGH — Item 14 (300-500 concurrent sandboxes) is unreachable — the provisioner hardcodes 3 orgs × 5 servers = 15
- Category: gap
- File: scripts/mcp_scale_provision.py:29
- Evidence: `ORGS` is a hardcoded 3-element literal (:29-33) and `SERVERS_PER_ORG` defaults to 5 (:24), so the manifest maxes at 15 targets; only servers-per-org is env-tunable, org count is not. The live report `mcp-parallel/findings/p8-26/multi_org_harness_report.json` shows targets=15. NPROC_ROOT_CAUSE.md shows the current infra already saturated the shared host-UID fork budget (~244/256) at just 15 servers, so 300-500 is not merely unprovisioned but not yet demonstrably feasible. Commit 5d0dd346's "scale matrix validated" overstates: cross-tenant isolation was only shown as 6/6 403 rejections at 15 MCPs.
- Maps-to: G5 item 14
- Verify: `grep -n 'ORGS = \[' -A5 scripts/mcp_scale_provision.py`. **Backstop-verified** (literal 3-tuple; `SERVERS_PER_ORG` default 5).
- Recommendation: Make org count env/manifest-driven, provision 30-50 orgs × 8-10 servers with distinct per-org UIDs (for a true per-tenant fork cap), and only mark item 14 done when manifest+live report show 300-500 healthy sandboxes concurrently.
- Corroboration: 2 auditors.

### HIGH — Item 15 (5k-10k concurrent tool calls) is unreachable — best harness peaks at 540 total / ~180 simultaneous across 15 targets
- Category: gap
- File: scripts/mcp_multi_org_harness.py:66
- Evidence: The P9.28 storm fires targets(15) × CONCURRENCY_DEPTH(12) per round × CONCURRENCY_ROUNDS(3) = 540 total / 180 simultaneous, workers capped at 240 (:66-71, 329-338). `mcp-parallel/findings/p9-28/concurrency_report.json` confirms submitted=540, returned=540. Nothing approaches 5k-10k, so routing correctness under that load is unproven at the required magnitude.
- Maps-to: G5 item 15
- Verify: `grep -nE 'CONCURRENCY_DEPTH|CONCURRENCY_WORKERS|MAX_WORKERS' scripts/mcp_multi_org_harness.py`.
- Recommendation: Scale the storm to 5k-10k in-flight calls (needs item 14 first) and keep the per-call id+canary demux assertions (dropped_no_response/id_mismatch/cross_target).
- Corroboration: 2 auditors.

### HIGH — Scale-matrix cross-tenant audit oracle is a dead no-op — `foreign_org_events` is structurally always 0 (asserts audit isolation without evidence)
- Category: mistake
- File: scripts/mcp_scale_matrix_live.py:129
- Evidence: Line 129 is `foreign = sum(1 for r in rows if any(f"/mcp/{fs}" in json.dumps(r) or r.get("org_slug") in other_slugs for fs in []))`. The inner generator iterates `for fs in []` (empty), so `any(...)` is always False, making the whole predicate dead and `foreign_org_events` (:130) unconditionally 0 regardless of real audit-row leakage — a verdict-without-evidence metric that can never trip. Compounding, `total_egress_bytes` (:143) sums `len(json.dumps(payload).encode())` — the REQUEST payload size, not real network egress.
- Maps-to: G5 item 19
- Verify: `python3 -c "print(any(1 for fs in []))"` → False; `sed -n '129,130p;143p' scripts/mcp_scale_matrix_live.py`. **Backstop-verified** (the empty `for fs in []` and always-0 `foreign` are present verbatim; intended code was `for fs in other_slugs`).
- Recommendation: Replace the dead generator with `foreign = sum(1 for r in rows if r.get('org_slug') in other_slugs)` (or `for fs in other_slugs`), rename `total_egress_bytes`→`request_bytes` (or capture actual sandbox egress), and run the leakage-canary matrix at 500-sandbox scale under chaos — cross-checked with an independent `aidefence` oracle over captured egress bytes — before claiming item 19.

### HIGH — Item 20 (1.4 guardrails under peak load): the live-matrix harness is fully sequential and never asserts redaction of egress bytes — a redact-but-forward passes as "allowed"
- Category: gap
- File: scripts/mcp_live_matrix_harness.py:172
- Evidence: `run_agent` loops calls sequentially (`for i in range(CALLS_PER_AGENT): ... await gateway_tools_call`, :164/167) and `main` awaits each agent one at a time (:213-214) — zero concurrency, so this is not a peak-load test. Classification is only allowed/blocked/errors via `_is_blocked` (:144-155); agents `B_pii`/`D_keypath` send real PII (email/ssn) but the harness never inspects response bytes for masking, so a path that logs a "redact" verdict yet forwards raw PII is counted as `allowed`. The only harness inspecting redaction bytes is `mcp_pipeline_matrix_live.py` (checks `[REDACT`/`***`, :54) at REPEAT=5 on a handful of cases — not peak; per-actor authz + tag enforcement are never exercised under load.
- Maps-to: G5 item 20
- Verify: `grep -nE 'for i in range|await run_agent|asyncio.gather' scripts/mcp_live_matrix_harness.py`; `grep -nE 'REDACT|redact|\*\*\*|has_pii' scripts/mcp_live_matrix_harness.py`.
- Recommendation: Make the guardrail matrix concurrent at peak scale and assert on response bytes — for every PII/IP/regulated case require the mask is present AND the raw token absent (cross-check with `aidefence_has_pii`), plus per-actor authz denials and tag-based enforcement.

### HIGH — Item 18 (chaos: kill sandbox/broker/Redis/PG + auto-recovery, no leak during recovery) has no harness — only mock-clock reaper unit tests exist
- Category: omission
- File: services/mcp-broker/tests/test_sandbox_reaper.py:14
- Evidence: No chaos harness exists (`ls scripts/ | grep -iE 'chaos|kill|redis|postgres'` empty). The only fault injection is unit-level in `test_sandbox_reaper.py`, which uses a `MockClock` (:14-23) and a MagicMock Docker client and only simulates `stop()` raising. Nothing kills a live broker/Redis/PG and asserts auto-recovery, and no during-recovery cross-tenant leakage canary is run. Backstop item 13 (auto-recovery/self-heal) is likewise unchecked.
- Maps-to: G5 item 18
- Verify: `ls scripts/ | grep -iE 'chaos|kill|redis|postgres|recover' || echo NONE`.
- Recommendation: Add a live chaos harness that kills each dependency mid-load, asserts self-heal (reaper/reconcile adopt orphans, 503→recovery), and runs the leakage canary throughout to prove no cross-tenant bleed during recovery.

### MEDIUM — Gateway Tier-1/2 policy BLOCK is gated on the tool/server scan_action POSTURE, not the matched rule's action — an actor-scoped `block` rule is silently downgraded to "tag" at default posture
- Category: mistake
- File: gateway/ai_mesh_gateway/mcp_scan_orchestrator.py:259
- Evidence: `_scan_text_tier1` (251-266) computes `eval_result = evaluate_mcp_policies(...)` but sets `blocked` ONLY via `if _enforce_blocks(enforcement): blocked = True` (:260-261); `eval_result.action == 'block'` is never used to block. `_enforce_blocks` returns True only when the resolved posture `enforcement == 'block'` (:200), and `default_scan_action` defaults to 'tag', so a rule authored `action='block'` and actor-scoped via `allowed_roles` is downgraded to detect-and-tag. Same in Tier-2 (:354-360). On the HTTP path the backend re-enforces the rule's block (engine.py:437), but the adapter (stdio/ws) path never reaches the backend.
- Maps-to: G2 item 3
- Verify: `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_scan_orchestrator.py -q`; call `scan_mcp_payload(..., enforcement='tag', ...)` with a rule authored `action='block'` → assert `McpScanResult.blocked is False`.
- Recommendation: On the gateway path, honor an actor-scoped rule's own `action='block'` even under a 'tag' posture.

### MEDIUM — Per-actor authorization primitive is an allowlist-SCOPE that cannot express deny-by-default and inverts intent for block policies
- Category: omission
- File: gateway/ai_mesh_gateway/policy_engine.py:200
- Evidence: `_policy_applies_to_actor` treats `allowed_user_ids/allowed_agent_ids/allowed_roles` as "this policy applies to these actors" — a matching actor keeps the policy, a non-matching known actor SKIPS it (:205-225). So a block policy with `allowed_roles=['admin']` BLOCKS admins and ALLOWS everyone else — the opposite of "restrict this tool to admins." There is no allow-only/deny-by-default per-actor tool authz.
- Maps-to: G2 item 3
- Verify: `_policy_applies_to_actor({'allowed_roles':['admin']}, {'roles':['admin']})` → True (block applies to admin), `(..., {'roles':['intern']})` → False (intern escapes the block).
- Recommendation: Add an explicit deny-by-default / allow-only-these-actors semantic (or an actor-keyed tool allowlist) and/or rename to `scoped_*`.

### MEDIUM — Outbound tool-RESULT scan is fail-OPEN and partial-coverage — raw result forwarded on scanner error (vs arg path fail-CLOSED), and only dict-shaped `result.content` is scanned
- Category: gap
- File: gateway/ai_mesh_gateway/mcp_proxy.py:735
- Evidence: `_scan_tool_result_floor` catches any scan exception and returns `result_content, False, [], [], {"result_scan_error": True}` (:730-735) — RAW result, not blocked — while the inbound twin `_scan_tool_args_block` fails CLOSED on the same error (`credential_force_block`, :670-677). Tier-2 result scan also fails open (mcp_scan_orchestrator.py:335-337) and the control-plane default `strict_mode` is `fail_open` (scan_controls.py:25). Separately, `ext_mcp_proxy` non-streaming scan only runs when `isinstance(data['result'], dict) and result.get('content') is not None` (:1080-1084), so a result that is a plain string or under `structuredContent` egresses unscanned.
- Maps-to: G2 item 2
- Verify: Read mcp_proxy.py:730-735 and 1080-1085; monkeypatch `_mcp_security_scan` to raise inside `_scan_tool_result_floor` → assert raw content returned.
- Recommendation: Make the result floor fail CLOSED (mask-all or block) on scan error, treat Tier-2 output outages as fail-closed for regulated-tag servers, and scan the entire result object rather than only dict `result.content`.
- Corroboration: 2 auditors.

### MEDIUM — Compliance tags are dropped from audit events when a tool call is BLOCKED on input — the highest-sensitivity path is untagged
- Category: gap
- File: control/ai_mesh_control/mcp_connector/views.py:1206
- Evidence: `_enforcement_compliance_tags` is computed only at views.py:1510 (post-execution) and passed to `_record_event` only on the success and output-block returns (:1529,1593). Every pre-execution block return records the `MCPEvent` WITHOUT `compliance_tags`: built-in policy block (:1206-1219), MCP-Firewall block (:1287-1299), tool_disabled (:1043-1050), tool_not_registered (:1066-1073), schema_validation_failed (:1088-1095).
- Maps-to: G2 item 5
- Verify: `grep -n 'compliance_tags=' control/ai_mesh_control/mcp_connector/views.py` → only 1529/1593/1704/2245.
- Recommendation: Compute input-stage tags from `eval_result.redaction_hints` at block time and pass `compliance_tags` to `_record_event` on the input-block paths.

### MEDIUM — npm/PyPI supply-chain: postinstall scripts disabled, but package pinning + allowlist default OFF and never propagated into the sandbox container; no registry lock
- Category: gap
- File: services/mcp-broker/src/sandbox/docker_manager.py:408
- Evidence: The container env sets `npm_config_ignore_scripts="true"` (:418, blocks postinstall RCE). But pin/allowlist controls live in the sandbox agent and default OFF: `_REQUIRE_PINNED_PACKAGES` = env 'false' (stdio_manager.py:58-60) and `_PACKAGE_ALLOWLIST` empty = allow-any (:49-53). The `_run_kwargs` env dict (:408-421) does NOT inject `MCP_STDIO_REQUIRE_PINNED_PACKAGES`/`MCP_STDIO_PACKAGE_ALLOWLIST`, so the in-container agent always runs with pinning off and no allowlist. The sandbox-image Dockerfile has no `.npmrc`/registry pin.
- Maps-to: G3 item 8
- Verify: `grep -n 'npm_config_ignore_scripts\|REQUIRE_PINNED\|PACKAGE_ALLOWLIST' services/mcp-broker/src/sandbox/docker_manager.py services/mcp-broker/sandbox-image/agent/stdio_manager.py`.
- Recommendation: Propagate `MCP_STDIO_REQUIRE_PINNED_PACKAGES=true` (or an org-scoped allowlist) into the sandbox via `_run_kwargs`, and bake a locked `.npmrc`/registry + `--ignore-scripts` into the sandbox image.

### MEDIUM — Host-run broker fallback places all org sandboxes on the shared bridge and publishes the agent port to the host, breaking per-tenant isolation
- Category: gap
- File: services/mcp-broker/src/sandbox/docker_manager.py:447
- Evidence: In `_run_kwargs`, when `_broker_container_ref() is None` (broker on the host) the code overrides the per-org network and attaches every sandbox to the single shared `self.config.network` (`mcp_sandbox_bridge`) and publishes `{f"{agent_port}/tcp": None}` (:447-450). All tenants then share one L2 bridge, so a compromised sandbox can reach sibling agents' `/rpc` on 9320 (cross-tenant), and the agent port is bound on the host. The `ensure_org_network` isolation is silently discarded.
- Maps-to: G3 item 7
- Verify: `sed -n '447,453p' services/mcp-broker/src/sandbox/docker_manager.py`.
- Recommendation: Even in host-run mode keep the per-org network and bind the published agent port to `127.0.0.1` only, or refuse to start unless the broker is containerized.

### MEDIUM — Item 17 (resource bombs mem/fork/disk/timeout contained + neighbors safe) is only an ad-hoc one-off, not a repeatable harness; disk/timeout and neighbor-safety unproven
- Category: incomplete
- File: scripts/ralph/mcp_progress.md:59
- Evidence: Coverage is a single manual `docker run` note (mcp_progress.md:59): "fork-bomb capped at ~63 procs BlockingIOError (pids_limit=64); mem-hog exit=137 OOM-killed (--memory=128m); host stayed healthy." No committed repeatable bomb harness, no disk-exhaustion or wall-clock-timeout bomb, and no assertion that NEIGHBOR sandboxes stayed responsive during the bomb.
- Maps-to: G5 item 17
- Verify: `grep -rniE 'fork.?bomb|stress-ng|dd if=|:\(\)\{ :' scripts/ services/mcp-broker/ | grep -v .venv`.
- Recommendation: Add a repeatable bomb harness covering all four vectors run while other tenants are under steady load, asserting both containment AND neighbor + host stay within SLO.

### MEDIUM — Item 16 (hours-long soak with reaper) has no soak harness — reaper only validated with a mock clock
- Category: omission
- File: services/mcp-broker/tests/test_sandbox_reaper.py:81
- Evidence: No soak driver exists (`ls scripts/ | grep -iE 'soak'` empty). Reaper correctness is asserted only against a `MockClock` advanced synthetically (:81-121) — never against wall-clock under sustained load, so real fd/mem/registry growth, pool exhaustion, or 503 storms over hours are never observed.
- Maps-to: G5 item 16
- Verify: `ls scripts/ | grep -iE 'soak' || echo 'no soak harness'`.
- Recommendation: Add an hours-long soak harness that sustains load across all sandboxes, samples fd/mem/pool/registry size + 503 rate over time, and proves the live reaper reclaims idle sandboxes without leaks (real clock).

### MEDIUM — Control-plane (in-app) tool-call redactions are invisible in the MCP panel — no execute-surface indicator and under-counted in the Redact StatCard
- Category: gap
- File: frontend/src/components/MCPConnectorPanel.jsx:1923
- Evidence: `renderExecute` keys the only verdict indicator off `executeResult.decision` (:1923-1927). But `/api/mcp-connector/tools/call/` sets `_final_decision = "monitor" if _output_monitor else "allow"` (control views.py:1577) — NEVER "redact" — and the success body omits `redacted_field_names`/`scan_action` (:1617). Result bytes ARE redacted first (views.py:1558/1566), so this is not a data leak, but the panel shows a green "Allow" badge with silently-masked values. Compounding, the Redact StatCard reads `(decisionCounts.redact||0)+(decisionCounts.monitor||0)` (:2207-2213) and `decision="redact"` is emitted ONLY by the gateway proxy path, so control-plane redactions never increment the headline indicator.
- Maps-to: G6 item 21
- Verify: `grep -n '_final_decision = \|return Response({"result": result' control/ai_mesh_control/mcp_connector/views.py`; read MCPConnectorPanel.jsx:1917-1946 and :2207-2213.
- Recommendation: Return an explicit redaction signal from tools/call (add `redacted_field_names`/`scan_action`, or a dedicated `decision:"redact"`) and render a Redact badge + redacted-field list in `renderExecute`; derive the Redact StatCard from `scan_action=='redact'`.

### MEDIUM — Durable Playwright UI gate never exercises item-21's three headline claims (compliance tags, per-actor controls, redaction indicators)
- Category: omission
- File: scripts/playwright_mcp_panels.mjs:266
- Evidence: `playwright_mcp_panels.mjs` asserts server-count honesty, register CRUD, scan-default PATCH, tool discovery, and tool execution with `assert(callBody.decision === "allow", ...)` (:266) + an on-screen "Allow" badge (:269). A grep of the whole gate for `decision|compliance|actor scope|allowed_user|allowed_role|redact|Advanced` returns ONLY the "allow" assertions — it never drives a redact/block decision, never asserts a `compliance_tags` Badge renders, and never opens the per-actor Actor Scope allowlists.
- Maps-to: G6 item 21
- Verify: `grep -niE 'decision|compliance|actor scope|allowed_user|allowed_role|redact|Advanced' scripts/playwright_mcp_panels.mjs`.
- Recommendation: Extend the gate: (1) configure a redact-posture rule and assert a Redact indicator + masked bytes; (2) assert a `compliance_tags` Badge renders for a tagged event; (3) open MCP Security Policies → New Policy and assert the Actor Scope inputs are present and persist.

### LOW — Mojibake in the per-actor "Actor Scope" section header — literal `Â·` renders instead of "·"
- Category: mistake
- File: frontend/src/components/PolicyManagementPanel.jsx:399
- Evidence: Line 399 is a JSX text child `Advanced Â· Actor Scope (MCP only)`; the file literally contains the mojibake bytes (U+00C2 U+00B7), not a clean U+00B7. So the header of the per-actor tool-control block renders the garbled string. Lines 358/376 have the same defect but sit inside JSX comments and do not render.
- Maps-to: G6 item 21
- Verify: `sed -n '399p' frontend/src/components/PolicyManagementPanel.jsx | od -c`.
- Recommendation: Replace the literal mojibake with an actual "·" character (or `{"·"}`).

## Priority order for backstop items

1. **G2 item 2 — fail-closed, byte-verified RESULT redaction.** Highest urgency: live raw PII/secret egresses today via the SSE `/ext-proxy` passthrough, the non-streaming string/`structuredContent` shapes, and the fail-OPEN result-floor error path. Fixing this closes the most direct data-leak class and is a precondition for any "1.4 guardrails" claim.
2. **G2 item 3 — per-actor tool authorization (and field-redaction parity) on the stdio/websocket adapter path.** Actor is never used for an access decision on adapter transports; also fix the posture-vs-rule block downgrade and the allowlist-scope inversion footgun. Unblocks the item-21 frontend per-actor claim.
3. **G2 item 5 — unify the compliance-tag vocabulary and add tag-driven enforcement.** The catalog is currently decorative (two disjoint vocabularies, ITAR/FERPA unreachable, INFRA uncataloged, gateway codes joining zero rows), no branch enforces on a tag, and input-block audit rows are untagged.
4. **G3 item 12 — real isolation: runsc + `RUNTIME_REQUIRED=true` and network-level egress default-deny.** The shipped default is fail-open (runc + open NAT bridge + cooperative env-proxy). Set the runtime + require it, enforce `internal=True`/iptables egress allowlists, and prove `Runtime=runsc` live.
5. **G3 item 7 — route http/sse/websocket through the per-org broker sandbox.** Only stdio is sandboxed; ws/http/sse still execute in the gateway/control backend. Add broker RPC routes and fix the host-run fallback that collapses per-tenant isolation onto one bridge.
6. **G5 item 19 — replace the dead cross-tenant leakage oracle before any leakage claim.** `foreign_org_events` is structurally 0 and `total_egress_bytes` measures request size — it fabricates an isolation "pass." Fix the predicate, capture real egress bytes, cross-check with an independent `aidefence` oracle.
7. **G5 items 14/15, then 18/16/17/20 — build the VERY-HARD stress suite at true scale.** Make org count manifest-driven for 300-500 sandboxes with distinct per-org UIDs, drive 5k-10k concurrent calls with per-call id/canary demux, and add chaos-kill/soak/resource-bomb/peak-load harnesses that assert redaction bytes — re-verifying the per-UID fork-budget ceiling.
8. **G6 item 21 — make redaction visible and gated.** Emit an explicit redact signal from `tools/call`, render a Redact badge + redacted-field list, fix the Redact StatCard under-count, extend the Playwright gate to cover tags/actor/redaction, and fix the mojibake in the Actor Scope header.
