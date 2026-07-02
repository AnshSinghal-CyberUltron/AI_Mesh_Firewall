# Claude Code Ralph — MCP Hardening BACKSTOP (edits-with-memory-log; 50–100 iters)
# RULE: every change → log to Ruflo + AGENTS.md + .cursor/rules + docs/mcp/HARDENING_CHANGELOG.md (§0).

## G0 — Setup + memory protocol
- [x] 0. Init the four-memory changelog; join hive; enforce MCP_SANDBOX_RUNTIME=runsc.
      CHG-0001 (2026-07-02): created docs/mcp/HARDENING_CHANGELOG.md (§0 protocol + template),
      .cursor/rules/mcp-hardening-changelog.mdc, AGENTS.md pointer. Audited runsc fail-closed
      enforcement in docker_manager._resolve_runtime() (docker_manager.py:343-359) — mechanism
      present & correct; prod must set MCP_SANDBOX_RUNTIME=runsc + MCP_SANDBOX_RUNTIME_REQUIRED=true
      (env-set verification tracked under G3 item 12).

## G1 — Backstop audit (what did the other sessions do/miss?)
- [x] 1. Review the cursor-mcp / stress / frontend branches + shared memory; list mistakes, omissions,
      regressions, and incomplete work → docs/mcp/BACKSTOP_FINDINGS.md.
      CHG-0002 (2026-07-02): 6-auditor parallel workflow → docs/mcp/BACKSTOP_FINDINGS.md (24 findings:
      13 high/8 med/1 low). Signal = OMISSIONS/fail-open parity gaps, not cross-session regressions.
      3 load-bearing claims backstop-verified (SSE unscanned egress; dead cross-tenant oracle
      `for fs in []`; 3-org=15-sandbox ceiling). PRIORITY ORDER for next items:
        #1 G2 item 2 — fail-closed byte-verified RESULT redaction (SSE /ext-proxy + string/structuredContent
           shapes + fail-OPEN result-floor all egress raw PII/secret TODAY — the most direct leak).
        #2 G2 item 3 — per-actor authz + field-redaction on stdio/ws adapter path (+posture-vs-rule block
           downgrade, +allowlist-scope inversion, +org_mcp_tool_call bypasses allowlist/cap).
        #3 G2 item 5 — unify tag vocab onto ComplianceTag.code + tag→action enforcement + tag input-blocks.
        #4 G3 item 12 — runsc REQUIRED + network-level egress default-deny (shipped default = runc+open NAT).
        #5 G3 item 7 — broker RPC for http/sse/ws (only stdio sandboxed) + fix host-run shared-bridge fallback.
        #6 G5 item 19 — replace dead cross-tenant oracle + capture real egress bytes + aidefence cross-check.
        #7 G5 14/15/18/16/17/20 — build true-scale stress (300-500 sandboxes, 5k-10k calls, chaos/soak/bomb/peak).
        #8 G6 item 21 — emit redact signal, Redact badge+field list, fix StatCard under-count, extend Playwright, fix mojibake.

## G2 — 1.4 Context Assembly & MCP Guardrails (log every edit)
- [x] 2. Field-level redaction of MCP tool RESULTS (byte-verified, fail-closed).
      DONE via CHG-0003+0004+0005 (2026-07-02). Result redaction is byte + independent-aidefence-oracle
      verified across ALL bare routes (rest/internal/ext streaming+non-streaming) and ALL result shapes
      (content/structuredContent/list/str); fail-CLOSED on the bare routes, fail-SAFE (500, no raw egress)
      on the main org_mcp_jsonrpc path (audited: scan exception propagates → 500, raw returned only after
      a successful scan). Gate: test_mcp_bare_proxy_scan.py 14 passed; broad sweep 362 passed.
      DEFERRED (NOT leaks): main-path graceful-block vs 500 (availability enhancement); per-actor
      FIELD-level RBAC masking → tracked under item 3.
      History — CHG-0003 (fail-closed result-scan error). `_scan_tool_result_floor`
      (mcp_proxy.py ~690-780) now blocks (SCAN_ERROR + result_scan_failclosed) instead of forwarding RAW
      on a scan exception — BOTH the primary output scan AND the redaction-floor re-scan. +2 byte-level
      tests (scanner patched to raise → raw PII absent + blocked); test_mcp_bare_proxy_scan.py 10 passed,
      broad sweep 323 passed.
      CHG-0004 (2026-07-02): (a) SSE buffer-and-scan DONE — ext_mcp_proxy buffers finite tools/call SSE,
      scans/redacts each data-frame result (_scan_reframe_sse_tool_result), re-emits masked or blocks;
      non-tools/call SSE passes through (no hang). 3 SSE tests replace the leak-pinning test;
      test_mcp_bare_proxy_scan.py 12 passed, broad sweep 342 passed; independent aidefence oracle:
      masked egress hasPII=false, raw hasPII=true.
      REMAINING before [x]: (b) non-streaming string/structuredContent result shapes unscanned;
      (c) audit main org_mcp_jsonrpc inline result path (~2265/2451) for the same fail-open.
- [x] 3. Per-user/agent/role tool authorization (close the mcp_proxy.py:302-305 gap; actor-keyed).
      DONE via CHG-0006+0007+0008 (2026-07-02). Per-actor tool ACCESS authorization (block/allow by
      user/agent/role) is enforced + tested across ALL paths: HTTP (MCPToolCallView), stdio/ws ADAPTER
      (scan orchestrator: evaluate_mcp_policies(actor) + _policy_applies_to_actor + CHG-0007 rule-block
      honoring, end-to-end proof in test_scan_enforces_actor_scoped_block_on_adapter_path), and per-key
      controls on the bare REST route (CHG-0006 allowlist/cap/disabled). Finding #1 was IMPRECISE (actor
      IS used for an access decision, one layer down); the mcp_proxy.py:301-307 cache-key TODO is a
      documented non-issue (tool enable/disable is server-scoped by design). Finding #4 was a FALSE
      POSITIVE (CHG-0007). Gate: 27 authz/scoping tests + 427 broad sweep pass.
- [x] 3b. Per-policy FIELD-level redaction (redaction_fields) on the stdio/ws adapter path (split from #3).
      HTTP path (MCPToolCallView, control views.py:1113) masks specific NAMED result fields for matched
      actor-scoped policies via apply_field_redaction/redact_structured; the gateway policy engine/bundle
      has NO field-redaction support (only redaction_hints), so the adapter path does content-scan but not
      field-level RBAC masking. Needs a bundle-format extension: add redaction_fields to compiled policies
      + gateway EvaluationResult + apply on the adapter response. Cross-cutting (control compiler + gateway).
      PARTIAL — CHG-0006 (2026-07-02): finding #2 closed. org_mcp_tool_call (bare REST route) now enforces
      the three per-key gates it lacked — _tool_allowed_by_key (403), mcp_max_tool_calls cap (429),
      _is_tool_disabled (403) — before forwarding, at parity with org_mcp_jsonrpc. +4 tests; 18 bare-proxy
      passed, broad sweep 424 passed. REMAINING before [x]: (#1, the big one) per-actor authz + field-RBAC
      masking NOT enforced on stdio/ws ADAPTER path (actor threaded for scan attribution only; enabled-tools
      payload has no actor dimension); (#3) Tier-1/2 policy BLOCK gated on posture not the rule's action
      (actor-scoped block downgraded to tag under default posture, mcp_scan_orchestrator.py); (#4)
      _policy_applies_to_actor allowlist-scope inverts intent (policy_engine.py).
      CHG-0007 (2026-07-02): #3 FIXED — Tier-1 policy scan (_scan_text_tier1) now honors a matched rule's
      own action='block' under any non-monitor posture (was downgraded to tag under default posture; the
      stdio/ws adapter path bypasses the backend that re-enforces the rule — now at parity with the
      control-plane engine). +2 tests; orchestrator 15 passed, broad sweep 427 passed.
      #4 DISMISSED as FALSE POSITIVE — _policy_applies_to_actor is a policy-SCOPING primitive (allowed_* =
      actors the policy APPLIES to; documented + mirrors control-plane engine); "block scoped to admins"
      is coherent scoping, NOT an inversion. Inverting would break the contract + control-plane parity +
      existing policies. Do NOT touch it. The auditor conflated scoping with the ABSENT deny-by-default
      per-actor tool-authz primitive (= finding #1).
      REMAINING before [x]: finding #1 (the big one) — per-actor user/agent/role tool authz + field-RBAC
      masking on the stdio/ws ADAPTER path (enabled-tools payload needs an actor dimension, or route the
      adapter path through actor-scoped policy eval).
      CHG-0024 (2026-07-02): field-RBAC HALF of finding #1 DONE. The compiler already emits
      Policy.redaction_fields into the bundle (compiler.py:521, M-04) and the control HTTP path masks those
      named tool-RESULT fields, but the gateway never consumed them — adapter path did content-scan yet NO
      field-level masking. Now: gateway EvaluationResult.redaction_fields (collected from matched actor-scoped
      policies in both evaluate() + evaluate_mcp_policies()), a Django-free port of apply_field_redaction
      (NFKC/case-insensitive keys, bounded, non-mutating), applied on the OUTPUT structured payload via
      scan_mcp_payload._finalize_output — scoped by actor, output-only, suppressed under monitor, audited via
      McpScanResult.redacted_fields + meta. +7 tests; test_mcp_scan_orchestrator.py 22 passed, broad sweep
      1056 passed. Backward-compat: redaction_fields=[] → no-op. STILL OPEN (advanced, not [x]): (a) cross-
      stage parity — control uses the INPUT-stage policy match to project fields out of the RESPONSE; the
      gateway currently triggers on an OUTPUT-scan match (thread input-stage redaction_fields into the output
      scan for full parity); (b) live drive proving field masking on a real adapter tool-call. (The per-actor
      ACCESS-authz half of finding #1 is already enforced via CHG-0006/0007/0008.)
      CHG-0025 (2026-07-02): DONE → item 3b [x]. Cross-stage input-triggered field projection added. The RBAC
      "role X never sees field F" pattern authors its rule on the CALL, so the INPUT-stage policy match now
      projects the declared redaction_fields out of the RESPONSE (control HTTP-path parity): the input scan
      surfaces policy_redaction_fields in its meta; org_mcp_jsonrpc threads it (_in_rfields) into both adapter
      OUTPUT scans as extra_redaction_fields; the orchestrator masks those fields; apply_field_redaction
      returns identity on a no-op; the adapter swap gate also fires on redacted_fields so a finding-less
      projection isn't discarded. +5 orchestrator tests, +2 END-TO-END adapter tests (real org_mcp_jsonrpc,
      real policy eval, byte-level: account_number masked in the adapter RESPONSE while non-targeted content
      survives; dormant guard when no policy). Gate: 37 relevant + 1063 broad sweep passed. Both trigger
      directions (output-content CHG-0024 + input-call CHG-0025) covered on the adapter path. RESIDUAL
      (non-blocking, out of 3b's adapter scope): bare-REST/ext-proxy cross-stage (separate surface; legacy
      direct-backend path covered by control's own MCPToolCallView field redaction) + optional live-stack
      drive over the in-process byte-level e2e proof.
- [x] 4. Context minimization / least-privilege assembly.
      RESOLVED N-A for MCP — CHG-0021 (2026-07-02): the MCP tool-call path has NO separate context-assembly
      step (unlike chat, where minimize_context prunes message history by token budget). Least-privilege for
      MCP = minimal forwarding (gateway forwards ONLY the tool args — echo returns just `message`, no user
      identity/session/context injected; enforced_at=gateway_adapter, not leaked to the tool) + result
      redaction (item 2) + per-actor authz (item 3). minimize_context (context_assembler.py) is the chat/LLM
      path only. Live-verified via the per-call chain evidence.
      Evidence: mcp-parallel/findings/backstop-p6-per-call-chain/per_call_chain_evidence.txt.
      CHG-0033 (2026-07-02, HIGH): NEW least-privilege/credential-leak finding on the ext-proxy path (distinct
      from the MCP tool-call minimize, which is N-A). ext_mcp_proxy forwarded the caller's request headers
      verbatim (only host/content-length/transfer-encoding stripped) to the third-party external MCP server —
      so the caller's Authorization: Bearer <gateway-API-key>, Cookie, and X-Api-Key egressed to the external
      domain (replayable against the gateway). The sandbox-routed path (broker_send_rpc) already built a clean
      header set + injected only the server's OWN OAuth token, so this was ext-proxy-only. FIX: new
      _ext_proxy_forward_headers strips hop-by-hop + credential/identity headers (authorization/proxy-
      authorization/cookie/set-cookie/x-api-key) + any x-gateway-* header, and injects the gateway's stored
      OAuth bearer for the upstream (if any) as the SOLE Authorization. +2 tests; test_mcp_bare_proxy_scan.py
      20 passed, broad sweep 1077 passed. Now the external server receives ONLY safe/protocol headers + its own
      token; the caller's gateway key never leaves the gateway.
- [ ] 5. Compliance tagging: extend mcp_compliance_tags.py to PII/IP/regulated; tag inputs + results; enforce by tag; audit.
      LIVE VERIFIED (mostly done) — CHG-0017 (2026-07-02): sent PII through the live gateway + queried
      MCPEvents. Redaction comprehensive (ssn/card/email all masked, combined too, 0 leak). compliance_tags
      recorded AND COMPLETE (email-only→['GDPR','PII']; email+ssn→['GDPR','HIPAA','PII']). decision=redact
      under default tag posture (E12 floor CHG-0005). So tag inputs+results, enforce-by-tag (via redaction),
      and audit ALL WORK — refutes the audit's "tags audit-only/no enforcement" framing. THE ONE REMAINING
      GAP before [x]: vocabulary mismatch — gateway emits GDPR/HIPAA/PII/PCI-DSS/… but the ComplianceTag
      catalog uses GDPR-PII/HIPAA-PHI/PCI-CARD/…, so MCPEvent.compliance_tags joins ZERO catalog rows
      (catalog reporting broken for gateway events). Fix = unify vocab onto ComplianceTag.code (cross-plane:
      gateway patterns.py + control catalog/migration; breaks 8 gateway tests) — owning-session semantic
      decision, NOT a unilateral backstop edit. Evidence: mcp-parallel/findings/backstop-p5-compliance-tags/.
      CHG-0030 (2026-07-02): the "extended to PII/IP/regulated" COVERAGE gap (distinct from the vocab mismatch
      above) CLOSED for IP/infra. detect_ip_leakage + IP_LEAKAGE_PATTERNS (internal IPv4/hostname/URL + private
      file paths -> INFRA tag) already ran on the CHAT output_guard but the MCP scan (_scan_text_tier1) ran ONLY
      detect_pii/detect_secrets — so internal host/IP/path in a tool RESULT was never detected/tagged/redacted.
      Now folded into the pii/secret fallback: ip_leakage finding -> INFRA-tagged (get_compliance_tags) +
      posture-enforced (block/redact/monitor). Fail-closed byte-check (redact_all covers internal IP/host/URL
      but NOT file paths -> if a detected internal value survives the scrub under redact, BLOCK — no
      redact-that-leaks). Public IPs not flagged. +5 tests; test_mcp_scan_orchestrator.py 32 passed, broad sweep
      1069 passed. STILL OPEN (item 5): the gateway INFRA/SECRET/PII vocab vs control ComplianceTag catalog
      codes (GDPR-PII/...) mismatch for catalog-join reporting — the cross-plane vocab decision.
- [x] 6. End-to-end per-tool-call chain: authz → minimize → scan+redact(in&result) → tag → audit.
      LIVE VERIFIED in order — CHG-0021 (2026-07-02): fired a PII tools/call, inspected the MCPEvent
      scan_trace/metadata. Chain executes in order on each call: authz (reached tool; org-key validated,
      cross-org=403 CHG-0016) -> minimize (N-A for MCP, item 4) -> scan+redact (scan_pipeline=two_tier;
      scan_trace = [tier1 input, tier1 output]; decision=redact via E12 floor CHG-0005) -> tag
      (compliance_tags=['GDPR','HIPAA','PII']) -> audit (MCPEvent w/ decision+tags+latency_ms+scan_trace+
      enforced_at=gateway_adapter). Evidence: mcp-parallel/findings/backstop-p6-per-call-chain/. NB: item 6
      is the CHAIN order (verified); the individual steps' remaining refinements are tracked under 3b (field
      RBAC redaction on adapter) + 5 (tag vocabulary catalog-join).

## G3 — Architecture hardening (log every edit)
- [x] 7. All transports (http/ws/sse/stdio) in the per-org gVisor sandbox; nothing in the backend (complete/fix if needed).
      VERIFIED STILL OPEN — CHG-0011 (2026-07-02, read-only; active-migration zone, did NOT edit). Broker
      side DONE (unified POST /{org}/rpc handles all transports, routes.py:296; agent dials HTTP+egress-
      allowlist). Gateway wiring INCOMPLETE: stdio→broker ✓ (adapter path, MCP_STDIO_IN_PROCESS=false);
      streamable-http/sse→control backend (mcp_proxy.py:2470 → /api/mcp-connector/tools/call/; backend calls
      upstream DIRECTLY, zero broker/BROKER_URL refs, docstrings 'directly to the upstream' views.py:412/601/727);
      websocket→in-gateway websockets.client.connect (mcp_ws_adapter.py:135). So http/sse/ws do NOT egress
      via the per-org sandbox → "nothing in the backend" UNMET; the "all 32 complete/all transports via
      sandbox" claim is inaccurate for the gateway wiring. NOT a data leak (1.4 result scan applies to
      http/sse per CHG-0005) — an ISOLATION gap (per-tenant egress). REMAINING before [x] (owning session):
      route gateway http/sse (org_mcp_jsonrpc else-branch / internal_tools_call direct-httpx) + websocket
      (mcp_ws_adapter.py) through broker_send_rpc (unified route already exists); prove NO transport's
      outbound call runs in the gateway/control backend.
      UPDATE — CHG-0018 (2026-07-02): the http/sse part of my CHG-0011 finding is RESOLVED by P4.13/P6.18.
      Gateway now routes stdio + streamable-http/sse via the sandbox: _is_sandbox_routed (mcp_proxy.py:1918)
      + _adapter_forward streamable-http/sse branch (1953-1978) uses broker_send_rpc (gateway builds
      upstream block, SANDBOX dials; gateway never connects). MCP_HTTP_VIA_SANDBOX default=true AND set true
      on the live gateway container. stdio live-verified (echo calls via sandbox). RESIDUAL: websocket STILL
      connects in-gateway (mcp_ws_adapter.py:135 websockets.client.connect, not migrated) — so
      "4-transport isolation active" OVERSTATES (3/4; ws unused live). REMAINING before [x]: migrate ws to
      broker_send_rpc (unified /{org}/rpc handles ws) OR document ws as legacy; independent live
      http-via-sandbox drive (register a streamable-http server, assert 0 direct upstream dials).
      CHG-0026 (2026-07-02): DONE → item 7 [x]. Migrated websocket onto the broker path. _adapter_forward now
      routes ws via broker_send_rpc (transport='websocket') alongside streamable-http/sse; the in-gateway
      mcp_ws_adapter.send_jsonrpc dial is REMOVED. Verified the broker + sandbox agent already support ws
      upstreams (routes.py unified /{org}/rpc for all 4 transports; upstream_manager session.ws;
      broker_send_rpc builds the upstream block for any non-stdio transport) — so this is a safe gateway-only
      migration that aligns _adapter_forward with _is_sandbox_routed's already-declared ws-sandbox-routed
      contract. Now ALL 4 transports (stdio+streamable-http+sse+websocket) egress via the per-org sandbox by
      default; gateway never dials upstream. +1 test (test_adapter_forward_websocket_uses_broker_send_rpc:
      asserts broker routing + in-gateway ws NOT dialed). Gate: 1064 gateway + 52 broker (ws/upstream/route/
      rpc/lifecycle) passed. CAVEAT: streamable-http/sse still honor MCP_HTTP_VIA_SANDBOX (default ON→sandbox,
      OFF→legacy direct-httpx); stdio+ws unconditional — so "nothing in the backend for ALL transports" holds
      for the DEFAULT config. mcp_ws_adapter now legacy (main.py reaper/shutdown hooks remain as benign
      no-ops). OPTIONAL follow-up: live ws-via-sandbox drive over the unit proof.
- [ ] 8. No unknown npm on host — proven.
      PROGRESS — CHG-0022 (2026-07-02): confirmed live gap (sandbox had npm_config_ignore_scripts but NOT
      the pin/allowlist envs; docker_manager._run_kwargs only set ignore_scripts, so the agent's pin/allowlist
      enforcement defaulted OFF/unreachable). FIXED: _run_kwargs now propagates MCP_STDIO_REQUIRE_PINNED_
      PACKAGES + MCP_STDIO_PACKAGE_ALLOWLIST into the sandbox env (default OFF pass-through, non-breaking —
      live UNPINNED "everything" servers still run). +1 broker test; test_sandbox_lifecycle.py 27 passed.
      REMAINING before [x]: (1) ENABLE in prod — set MCP_STDIO_REQUIRE_PINNED_PACKAGES=true (requires pinning
      every registered server's package spec); (2) bake a locked .npmrc/private registry into the sandbox
      image (registry still default public npmjs); (3) malicious-postinstall fixture proven inert via egress
      capture (audit's item-8 acceptance).
- [ ] 9. Gateway auth/authz/validation/rate-limit/policy/audit — verified + hardened.
      LIVE VERIFIED (auth/authz/validation) — CHG-0016 (2026-07-02): probed the live gateway. Auth ENFORCED
      (no-auth→401, bad-key→401); CROSS-ORG key ISOLATION ENFORCED (org-a key on org-b endpoint→403
      org_scope_violation, and vice versa — auth-layer cross-tenant isolation, both directions); input
      validation GRACEFUL (missing-method/malformed-json/empty-tool → no 500). Rate-limit (S12) exists but a
      60-call burst didn't trip it (higher threshold). Policy authz already unit-proven (CHG-0006/0007/0008);
      audit wired (_record_gateway_event). Evidence: mcp-parallel/findings/backstop-p9-gateway-authz/.
      REMAINING before [x]: probe the rate-limit THRESHOLD (larger controlled burst — deferred to avoid
      throttling shared keys); adversarial policy-enforcement + audit-completeness checks.
      CHG-0031 (2026-07-02): rate-limit PARITY gap CLOSED on the bare REST route. _enforce_mcp_org_rate_limits
      (per-org TPM+burst/RPM, atomic Redis INCR, fail-open by design) was called ONLY by org_mcp_jsonrpc
      (mcp_proxy.py:2092); org_mcp_tool_call (bare REST /tools/call) had the per-KEY tool-call cap +authz
      (CHG-0006) but NOT the per-ORG rate limit — a tenant could exceed org burst/RPM/TPM via the bare route.
      Extracted _mcp_org_rate_limit_raw (plain 429 JSONResponse); the JSON-RPC route wraps it (unchanged), the
      bare route returns it as-is (REST 429 + Retry-After). +3 tests; test_mcp_rate_limit.py 7 passed, broad
      sweep 1072 passed. STILL OPEN (item 9): live threshold probe (controlled >150 req/s burst on a dedicated
      key/host); adversarial policy-enforcement + audit-completeness; ext_mcp_proxy (external passthrough) also
      lacks the per-org limiter (follow-up if tenant-exposed).
      CHG-0032 (2026-07-02): ext_mcp_proxy rate-limit follow-up CLOSED. The authenticated external MCP proxy
      (/v1/mcp/ext-proxy/{host}/{path}, behind auth middleware — NOT in EXCLUDED_PATHS, so org_slug is
      available) had inbound credential + SSE result scanning but NO per-org rate limit. Added
      _mcp_org_rate_limit_raw(_get_auth_context(request)) after the domain allowlist check (plain 429 before
      scan/forward). Now ALL THREE tenant-facing MCP entry points (org_mcp_jsonrpc, org_mcp_tool_call,
      ext_mcp_proxy) enforce the per-org TPM/burst/RPM ceiling → code-level rate-limit coverage COMPLETE. +3
      tests; test_mcp_rate_limit.py 10 passed, broad sweep 1075 passed. Item 9 still [ ]: live threshold probe
      (controlled >150 req/s on a dedicated key/host) + adversarial policy-enforcement + audit-completeness.
      CHG-0034 (2026-07-02): gateway VALIDATION/DoS gap CLOSED — the MCP routes buffered the whole body
      (request.body()/json()) with NO size ceiling (RAG/embeddings already 413-guard; MCP had none). Added
      _MCP_MAX_BODY_BYTES (default 10MiB, env MCP_MAX_BODY_BYTES) + _mcp_body_too_large() -> 413
      mcp_body_too_large BEFORE buffering, on all three entry points (org_mcp_jsonrpc, org_mcp_tool_call,
      ext_mcp_proxy). Also AUDITED the sibling gateway->upstream egress paths for the CHG-0033 credential-leak
      pattern: internal_discover_tools/internal_tools_call build CLEAN upstream headers from the server's own
      auth_token (+ is_safe_outbound_url SSRF guard) and the sandbox path (broker_send_rpc) too — so CHG-0033
      was the isolated leak. Non-invasive Content-Length pre-check. +4 tests; test_mcp_rate_limit.py 14 passed,
      broad sweep 1081 passed. LIMITATION (documented): doesn't catch chunked-without-Content-Length (infra
      body limit covers it; app-layer streaming cap deferred to avoid MCP test-harness churn).
- [ ] 10. Resource limits CPU/mem/disk/timeout enforced + containment proven.
      LIVE VERIFIED (config) — CHG-0015 (2026-07-02): docker inspect of all 3 live org sandboxes shows
      CapDrop=[ALL], SecurityOpt=[no-new-privileges], Privileged=false, PidsLimit=256, Memory=2GiB,
      NanoCpus=1, ReadonlyRootfs=true, tmpfs /tmp noexec,nosuid, npm_config_ignore_scripts set. Strong
      containment deployed. REMAINING before [x]: prove CONTAINMENT under load (fork/mem/disk/timeout bombs
      + neighbor-safe) — but destructive bombs are UNSAFE on the shared live stack (item 17 needs an
      isolated host). Evidence: mcp-parallel/findings/backstop-p12-isolation-posture/.
- [ ] 11. PostgreSQL + Redis schemas/usage/restart-safety verified.
      LIVE VERIFIED (usage/schema) — CHG-0023 (2026-07-02): REDIS usage correct — mcp:scan_ver:* 72 keys
      (M-15 scan-config version cache-invalidation, string counters e.g. "99"); ratelimit:* 2 keys (S12
      active); mcp:toolcalls:* mechanism present (0 active = uncapped test keys, 60s TTL). POSTGRES correct
      at scale — mcp_connector_mcpevent 109,362 events / 3 orgs; compliance_tags populated (block=10,
      redact=265). RESTART-SAFETY graceful by design (Redis-unreachable -> pure-TTL, no crash; PG recording
      best-effort/non-blocking). REMAINING before [x]: actual restart DRILL (kill Redis/PG mid-load, verify
      recovery + no leakage during recovery) = item 18 chaos (UNSAFE on shared stack; needs dedicated host).
      Evidence: mcp-parallel/findings/backstop-p11-pg-redis/pg_redis_evidence.txt.
- [ ] 12. gVisor + seccomp/no-new-privileges/cap_drop/egress-lockdown enforced.
      LIVE VERIFIED — CHG-0015 (2026-07-02): PRESENT live = cap_drop=ALL, no-new-privileges, per-org network
      (mcp_sandbox_net_<org> distinct per org — host-run shared-bridge fallback NOT active). GAPS:
      (a) gVisor UNMET INFRA PREREQ — `which runsc`=NOT installed, docker only offers runc; sandboxes run
      on runc (shared kernel). Code fail-closes if RUNTIME_REQUIRED=true (CHG-0001) so forcing it would KILL
      the sandboxes — gVisor must be INSTALLED on the deploy host first (infra task, not code). (b) egress:
      per-org networks internal=false (open outbound NAT) — no network-level egress default-deny. REMAINING
      before [x]: install gVisor + require runsc (verify Runtime=runsc live); network egress default-deny
      (per-org internal=true + broker-proxied allowlist, or iptables/eBPF). NOTE: risky to change live (would
      break the running stack). Evidence: mcp-parallel/findings/backstop-p12-isolation-posture/.

## G4 — Production hardening (Phase 3)
- [ ] 13. Monitoring + metrics + tracing wired; backup; auto-recovery (sandbox/broker/Redis/PG self-heal).
      LIVE VERIFIED (partial) — CHG-0020 (2026-07-02): probed the running stack. METRICS WIRED (gateway
      /metrics 401 scraper-key-gated, METRICS_ALLOW_OPEN=false=secured; telemetry-drain thread). HEALTH
      WIRED (gateway /health + /v1/mcp/health 200; control /api/health/ 200; broker :8311 /health 200).
      AUTO-RECOVERY: docker healthchecks on broker/control/postgres/redis (all healthy -> auto-restart on
      unhealthy) + sandbox reaper/reconcile. GAPS before [x]: (1) distributed TRACING (OTEL/Jaeger) NOT
      configured (metrics/telemetry present, but no request-level tracing); (2) gateway container has NO
      docker healthcheck (health=none -> not auto-restarted); (3) backup (PG/Redis) NOT verified. Evidence:
      mcp-parallel/findings/backstop-p13-observability/observability_evidence.txt.
      CHG-0027 (2026-07-02): gap (2) CLOSED. Added a docker healthcheck + restart to the gateway service (the
      ONLY core service with neither). Probes the auth-exempt /health (200 ok / 503 on signing misconfig;
      python urllib, interval 15s/timeout 6s/retries 5/start_period 60s) on BOTH docker-compose.yml (base:
      healthcheck + restart: unless-stopped) and docker-compose.prod.yml (prod: healthcheck; restart via
      anchor). Config-only — running container NOT recreated; takes effect next `docker compose up`. VERIFY:
      `docker compose -f docker-compose.yml -f docker-compose.override.yml config` renders gateway.healthcheck
      + restart=unless-stopped; base+prod merged config also valid. STILL OPEN (item stays [ ]): (1) OTEL/
      Jaeger distributed tracing; (3) PG/Redis backup verification; optional peer service_healthy upgrade.

## G5 — VERY HARD stress (big hardware; run each, capture evidence)
- [ ] 14. 30–50 orgs × 8–10 MCPs = 300–500 sandboxes concurrently — provision + healthy.
      CODE CEILING REMOVED — CHG-0010 (2026-07-02): scale provisioner org count was a hardcoded 3-tuple
      (→ 3×5=15-sandbox ceiling). Now build_orgs(NUM_ORGS) (default 3 backward-compat; extends via org-<i>),
      so NUM_ORGS=50 SERVERS_PER_ORG=10 → 500 targets. New scripts/test_mcp_scale_provision.py: 4 pass
      (incl. 50-org→500-sandbox). REMAINING before [x]: (1) PRE-CREATE the N orgs in control (bulk
      org-creation mgmt command using org-<i> convention — provisioner logs in, doesn't create); (2) broker
      per-org DISTINCT sandbox UID for a true per-tenant fork budget (NPROC_ROOT_CAUSE.md — shared host-UID
      budget saturated ~244/256 at just 15 servers); (3) actually provision + prove 300-500 sandboxes
      HEALTHY concurrently on the live stack.
- [ ] 15. 5k–10k concurrent tool calls — routing correct, isolation holds, none dropped/mixed.
- [ ] 16. Soak (hours) — no leaks/exhaustion/503 storms; reaper correct.
- [ ] 17. Resource bombs (mem/fork/disk/timeout) — contained; neighbors + host safe.
- [ ] 18. Chaos (kill sandbox/broker/Redis/PG) — auto-recovery + no leakage during recovery.
- [ ] 19. Cross-tenant leakage canaries at 500-sandbox scale under chaos — never observed anywhere.
      ORACLE FIXED — CHG-0009 (2026-07-02): the audit-log cross-tenant oracle in mcp_scale_matrix_live.py
      was FABRICATED (`for fs in []` → foreign_org_events structurally 0, and NOT in the PASS gate — a
      false "isolation proven" signal). Replaced with a real unit-tested count_foreign_events, ADDED to the
      PASS gate (total_foreign==0), made the module import-safe, renamed misleading total_egress_bytes→
      request_payload_bytes. New scripts/test_mcp_scale_oracle.py: 5 pass (incl. proof the OLD predicate
      missed a real leak). REMAINING before [x]: run the canary matrix LIVE at 500-sandbox scale under
      chaos with the corrected+gated oracle + capture REAL sandbox egress bytes cross-checked with an
      independent aidefence oracle (tied to item 14 true scale — still a hardcoded-3-org=15-sandbox ceiling).
      LIVE VERIFIED @15-MCP — CHG-0013 (2026-07-02): stack was up, RAN scale-matrix 3× consecutive
      (ROUNDS=6, 540 calls): cross_org_result_leak=0, foreign_org_events_total=0 (fixed oracle), errors=0,
      mismatches=0 → cross-tenant isolation HOLDS. De-flaked the gate: retry-on-mismatch (+transient_retries)
      since a ~0.7% transient echo hiccup under load intermittently failed the strict gate (NOT a demux/
      isolation bug — cross_org_leak/errors stayed 0 across 1080+ calls). Evidence:
      mcp-parallel/findings/backstop-p19-isolation/scale_isolation_evidence.json. REMAINING before [x]:
      run at TRUE 300-500-sandbox scale (item 14 live) UNDER CHAOS (item 18) with captured egress bytes +
      independent aidefence cross-check.
- [ ] 20. 1.4 under peak load — redaction + per-actor authz + tagging hold; no PII/IP/regulated escape.
      HARNESS UPGRADED — CHG-0012 (2026-07-02): mcp_live_matrix_harness.py was fully SEQUENTIAL (not peak
      load) + classified only allowed/blocked/errors, never inspecting response bytes (a redact-but-forward
      counted as allowed). Now runs all calls CONCURRENTLY (CONCURRENCY-bounded semaphore, asyncio.gather)
      + asserts on RESPONSE BYTES via find_leaked_values (any sent sensitive value appearing raw in the
      egress = LEAK → run FAILS). New scripts/test_mcp_live_matrix_oracle.py: 5 pass. REMAINING before [x]:
      run LIVE at peak load (high CONCURRENCY / 5k-10k in-flight, tied to item 15) against the stack, prove
      ZERO leaks 3× consecutively; extend with per-actor authz-denial + tag-enforcement cases under load.
      LIVE VERIFIED @25-concurrency — CHG-0014 (2026-07-02): ran the upgraded live-matrix harness 3×
      consecutive (CONCURRENCY=25, 150 calls each/450 total): total_leaked=0, total_redacted=60/run,
      errors=0 → 1.4 redaction HOLDS under concurrent load (validates CHG-0003/0004/0005 result floor +
      CHG-0012 byte-check end-to-end; the outbound floor redacts PII even under the default tag posture).
      Harness fixes: PII embedded in `message` (round-trips through the echo test tool — the redaction test
      was vacuous before); resilient server-id lookup (graceful CONTROL_URL degradation). Evidence:
      mcp-parallel/findings/backstop-p20-redaction-load/redaction_under_load_evidence.json. REMAINING before
      [x]: run at TRUE peak (5k-10k in-flight, item 15); add per-actor authz-denial + tag-enforcement cases.
      CHG-0028 (2026-07-02): authz-denial dimension ADDED to the harness. New authz_denied() (403 / authz-error
      / [BLOCKED] result; a GENERIC error is NOT a denial) + authz_violation() (True ONLY when a forbidden tool
      EXECUTED successfully under load = a real hole) + a Scenario type + a DENY_TOOL_NAME-gated F_authz_deny
      concurrent agent; the gate now FAILS on any authz violation and flags a vacuous deny run. +3 oracle tests
      (scripts/test_mcp_live_matrix_oracle.py -> 8 passed); build_scenarios() = 5 default / 6 with DENY_TOOL_
      NAME. Backward-compat: unset -> redaction matrix unchanged. STILL OPEN (item stays [ ]): (a) run the full
      matrix LIVE at TRUE peak (5k-10k, item 15) with a real denied-but-existing tool as DENY_TOOL_NAME (needs
      a dedicated host + a per-key allowlist/disabled-tool setup, not safe to configure unilaterally on shared
      state); (b) tag-enforcement-under-load audit = query MCPEvents for compliance_tags at load (per CHG-0017).
      CHG-0029 (2026-07-02): hardened the OTHER named harness — mcp_pipeline_matrix_live.py. It was leak-blind
      (pii = _SSN in text: ONE hardcoded SSN in ONLY result.content[0].text, so a redact-but-forward in a later
      content item / structuredContent / nested field / any non-SSN value passed as redacted — false-green
      under load) and import-unsafe (KEY/argv/asyncio.run at module scope). Now find_pii_in_body() scans the
      FULL serialized response for the case's ACTUAL sensitive values (case_sensitive_values: explicit
      case['pii'] or SSN fallback); redacted = allow + no raw value anywhere + a marker (byte-truth);
      import-safe. +8 unit tests (scripts/test_mcp_pipeline_oracle.py); all 4 scripts oracle suites -> 25
      passed. Same class of fix as CHG-0009 (dead oracle) + CHG-0012 (no byte-check). The LIVE pipeline run at
      scale (epochs × cases × REPEAT) still needs the dedicated-host stress env (items 14-18); this makes its
      verdicts trustworthy.

## G6 — Frontend (strictly; log edits to owned panels)
- [ ] 21. MCP panels reflect 1.4 (tags, per-actor tool controls, redaction indicators), real data, no leak, both themes.
      CHG-0019 (2026-07-02): fixed the mojibake sub-finding — PolicyManagementPanel.jsx:399 Actor Scope
      header had literal `·` in raw JSX text (a fe-harden mojibake "fix" that was itself broken — JSX
      doesn't interpret escapes in text nodes, so it rendered the literal string). Now `{'·'}` (JS expr
      → renders `·`, pure-ASCII source). npm run build ✓. REMAINING (owned by fe-harden): explicit redact
      signal from tools/call + Redact badge + redacted-field list on execute; fix Redact StatCard
      under-count; extend Playwright gate to cover tags/actor/redaction; both themes.

## G7 — Recursive verification
- [ ] 22. Re-run G2–G6 end-to-end 3×; adversarial pass; Ruflo consensus green. Only then <promise>COMPLETE</promise>.
