# AGENTS.md — AI Mesh Firewall

## MCP Hardening BACKSTOP changelog
- Parallel Claude + Cursor sessions harden the multi-tenant MCP gateway. **Every hardening change is
  logged to four memories in the SAME commit:** Ruflo (`mcp__ruflo__memory_store`
  namespace=`mcp-hardening/changes` + `hooks_notify`), this file (one-line pointer), Cursor
  (`.cursor/rules/mcp-hardening-changelog.mdc`), and the canonical `docs/mcp/HARDENING_CHANGELOG.md`
  (source of truth; §0 = protocol + entry template).
- Backstop work-list: `scripts/ralph/mcp_hardening_scratchpad.md` (G0–G7, one item/iteration).
- Shared index: stage narrowly, commit immediately, never `git add -A`; rebase from `main` often.
- Changelog pointers (newest last):
  - CHG-0001 (2026-07-02) — established the four-memory changelog protocol; audited runsc fail-closed
    enforcement in `docker_manager._resolve_runtime()`.
  - CHG-0002 (2026-07-02) — backstop audit → `docs/mcp/BACKSTOP_FINDINGS.md` (24 findings; next priority =
    G2 item 2 fail-closed byte-verified result redaction). Confirmed SSE egress unscanned, per-actor authz
    missing on stdio/ws, tags audit-only, gVisor/egress default fail-open, a dead cross-tenant oracle +
    15-sandbox ceiling overstating prior "scale validated" claims.
  - CHG-0003 (2026-07-02) — G2 item 2 (partial): `_scan_tool_result_floor` now fails CLOSED on result-scan
    error (was fail-OPEN, forwarding raw RESULTS). +2 byte-level tests; 323 passed. Remaining: SSE
    buffer-and-scan, string/structuredContent shapes, main-path inline audit.
  - CHG-0004 (2026-07-02) — G2 item 2 (closes HIGH SSE leak): `ext_mcp_proxy` now buffers+scans finite
    tools/call SSE results (`_scan_reframe_sse_tool_result`), re-emits masked or blocks; non-tools/call SSE
    passes through. 12 + 342 tests pass; aidefence oracle confirms clean egress. Remaining: string/
    structuredContent shapes, main-path inline audit.
  - CHG-0005 (2026-07-02) — CLOSES G2 item 2: ext-proxy (non-streaming + SSE) scans the WHOLE `result`
    (content/structuredContent/list/str), not just `result.content`. Main `org_mcp_jsonrpc` audited
    fail-SAFE (scan error → 500, raw only after successful scan), not fail-open. 14 + 362 tests pass.
    Item 2 DONE (fail-closed bare / fail-safe main, byte+oracle verified). Next: G2 item 3 per-actor authz.
  - CHG-0006 (2026-07-02) — G2 item 3 (finding #2): `org_mcp_tool_call` REST route now enforces the three
    per-key gates (allowlist 403 / call-cap 429 / disabled 403) before forwarding, at parity with
    `org_mcp_jsonrpc`. +4 tests; 18 + 424 pass. Remaining item 3: per-actor authz + field RBAC on stdio/ws
    adapter path (finding #1); posture-vs-rule block downgrade (#3); allowlist-scope inversion (#4).
  - CHG-0007 (2026-07-02) — G2 item 3 (#3 fixed, #4 dismissed): Tier-1 policy scan honors a rule's own
    `action='block'` under any non-monitor posture (was downgraded to tag; adapter path bypasses the backend
    — parity with control-plane engine). **#4 FALSE POSITIVE** — `_policy_applies_to_actor` is correct
    policy-SCOPING (mirrors control plane); do NOT invert it. +2 tests; 15 + 427 pass. Remaining item 3:
    finding #1 (per-actor authz + field RBAC on stdio/ws adapter path).
  - CHG-0008 (2026-07-02) — G2 item 3 AUTHZ DONE (finding #1 CORRECTED): per-actor ACCESS authz
    (block/allow by user/agent/role) IS enforced on the stdio/ws adapter path via `evaluate_mcp_policies(actor)`
    + `_policy_applies_to_actor` (+CHG-0007) — the audit's "no access decision on adapter path" was
    imprecise. Added end-to-end proof (27 pass). SPLIT-OUT item 3b: per-policy FIELD-level redaction
    (`redaction_fields`) is HTTP-only; needs a gateway bundle-format extension.
  - CHG-0009 (2026-07-02) — G5 item 19 (oracle fixed): the cross-tenant leakage oracle in
    `scripts/mcp_scale_matrix_live.py` was FABRICATED (`for fs in []` → always 0, and not gated). Replaced
    with a real unit-tested `count_foreign_events`, added to the PASS gate, import-safe; new
    `scripts/test_mcp_scale_oracle.py` (5 pass). Remaining: run the canary matrix LIVE at 500-sandbox scale
    with real egress + aidefence cross-check (tied to item 14).
  - CHG-0010 (2026-07-02) — G5 item 14 (code ceiling removed): scale provisioner org count was hardcoded
    3-tuple (→15-sandbox ceiling); now `build_orgs(NUM_ORGS)` (default 3), so `NUM_ORGS=50 SERVERS_PER_ORG=10`
    → 500 targets. New `scripts/test_mcp_scale_provision.py` (4 pass). Remaining: pre-create N orgs; broker
    per-org distinct UID (fork budget); prove 300-500 healthy live.
  - CHG-0011 (2026-07-02) — VERIFICATION (docs only): G3 item 7 is broker-complete but gateway-wiring
    INCOMPLETE — corrects the "all 32 complete / all transports via sandbox" claim. stdio→broker ✓;
    http/sse→control backend (calls upstream directly, no broker); ws→in-gateway. So http/sse/ws don't
    egress via the per-org sandbox. NOT a data leak (result scan applies per CHG-0005) — isolation gap.
    Owning session: route http/sse + ws through `broker_send_rpc` (unified `/{org}/rpc` route ready).
  - CHG-0012 (2026-07-02) — G5 item 20 harness upgrade: `mcp_live_matrix_harness.py` was sequential + never
    checked response bytes (redact-but-forward counted as allowed). Now concurrent (CONCURRENCY semaphore) +
    asserts RESPONSE BYTES via `find_leaked_values` (raw sent value in egress = leak → FAIL). New
    `scripts/test_mcp_live_matrix_oracle.py` (5 pass). Remaining: run LIVE at peak load 3×, zero leaks.
  - CHG-0013 (2026-07-02) — G5 item 19 LIVE isolation VERIFIED: stack up → RAN scale-matrix 3× consecutive
    (540 calls): cross_org_result_leak=0, foreign_org_events_total=0, errors=0, mismatches=0. De-flaked the
    gate (retry-on-mismatch; the ~0.7% transient echo hiccup was flakiness, not a demux/isolation bug).
    Evidence in mcp-parallel/findings/backstop-p19-isolation/. Remaining: 500-sandbox-under-chaos scale.
  - CHG-0014 (2026-07-02) — G5 item 20 LIVE redaction-under-load VERIFIED: ran live-matrix 3× consecutive
    (CONCURRENCY=25, 450 calls): total_leaked=0, redacted=60/run, errors=0 — 1.4 redaction holds under
    concurrency (validates CHG-0003/0004/0005 + CHG-0012 end-to-end). Fixes: PII in `message` (round-trips
    through echo), resilient CONTROL_URL lookup. Evidence mcp-parallel/findings/backstop-p20-redaction-load/.
  - CHG-0015 (2026-07-02) — LIVE architecture posture (docker inspect): item 10 resource limits VERIFIED
    STRONG (cap_drop=ALL, no-new-priv, pids=256, mem=2GiB, cpu=1, readonly-rootfs, noexec-tmpfs); per-org
    network isolation VERIFIED (distinct mcp_sandbox_net_<org>). item 12 gVisor UNMET INFRA PREREQ
    (runsc NOT installed — can't require here); egress GAP confirmed (networks internal=false). Evidence
    mcp-parallel/findings/backstop-p12-isolation-posture/.
  - CHG-0016 (2026-07-02) — G3 item 9 LIVE gateway auth/authz VERIFIED: no-auth/bad-key→401, CROSS-ORG
    key→403 org_scope_violation (both directions = auth-layer cross-tenant isolation), bad input→graceful
    (no 500). Rate-limit exists (S12) but not tripped at 60 calls. Evidence
    mcp-parallel/findings/backstop-p9-gateway-authz/. Remaining: rate-limit threshold + adversarial policy/audit.
  - CHG-0017 (2026-07-02) — G2 item 5 LIVE compliance-tagging verified; narrowed to vocabulary-only gap.
    Redaction comprehensive (ssn/card/email masked, 0 leak); tags recorded+complete (email+ssn →
    ['GDPR','HIPAA','PII']); decision=redact under tag posture. ONLY gap: gateway codes ≠ catalog codes so
    MCPEvent.compliance_tags joins 0 catalog rows. Fix = unify vocab (cross-plane) — owning session.
    Evidence mcp-parallel/findings/backstop-p5-compliance-tags/.
  - CHG-0018 (2026-07-02) — RE-VERIFY G3 item 7: my CHG-0011 http/sse-in-backend finding RESOLVED by
    P4.13/P6.18 (gateway routes stdio + streamable-http/sse via sandbox `broker_send_rpc`, never dials
    upstream; MCP_HTTP_VIA_SANDBOX=true live). RESIDUAL: websocket still in-gateway (mcp_ws_adapter.py:135),
    so "4-transport" overstates (3/4; ws unused live). Remaining: migrate ws to broker or mark ws legacy.
  - CHG-0019 (2026-07-02) — item 21 (frontend): fixed a BROKEN mojibake fix at PolicyManagementPanel.jsx:399
    (literal `·` in raw JSX text rendered the string, not `·`); now `{'·'}`. npm run build ✓.
  - CHG-0020 (2026-07-02) — G4 item 13 LIVE observability: metrics + health + auto-recovery (docker
    healthchecks + reaper) WIRED. GAPS: distributed tracing (OTEL) not configured; gateway has no docker
    healthcheck; backup unverified. Evidence mcp-parallel/findings/backstop-p13-observability/.
  - CHG-0021 (2026-07-02) — G2 items 4 & 6 RESOLVED: LIVE per-call chain verified in order (authz → minimize
    [N-A for MCP] → scan+redact [scan_trace tier1 in→out, decision=redact] → tag [GDPR,HIPAA,PII] → audit).
    item 4 minimize = N-A (gateway forwards only tool args; minimize_context is chat-only). Only G2 open: 3b
    (field RBAC) + 5 (vocab). Evidence mcp-parallel/findings/backstop-p6-per-call-chain/.
  - CHG-0022 (2026-07-02) — G3 item 8: `docker_manager._run_kwargs` now propagates the npm pin/allowlist
    env (MCP_STDIO_REQUIRE_PINNED_PACKAGES/PACKAGE_ALLOWLIST) into the sandbox (default OFF pass-through) so
    the agent's enforcement is reachable; was unreachable (defaulted OFF). +1 broker test; 27 passed.
    Remaining: enable in prod (pin all servers) + locked .npmrc/registry in sandbox image.
  - CHG-0023 (2026-07-02) — G3 item 11 LIVE PG+Redis correctness: Redis usage correct (scan_ver 72 keys
    M-15 cache-invalidation; ratelimit 2 keys S12; toolcalls mechanism present); PG persistence correct at
    scale (109,362 MCPEvents/3 orgs; compliance_tags populated). Restart-safety graceful by design; actual
    restart-drill = item-18 chaos (unsafe on shared stack). Evidence mcp-parallel/findings/backstop-p11-pg-redis/.
  - CHG-0024 (2026-07-02) — G2 item 3b: per-policy FIELD-level RBAC redaction on the stdio/websocket ADAPTER
    path. Gateway now consumes each matched actor-scoped policy's `redaction_fields` (emitted into the bundle
    by M-04, compiler.py:521) — `EvaluationResult.redaction_fields` + a Django-free port of control's
    `apply_field_redaction` (NFKC/case-insensitive keys, bounded, non-mutating) mask the named tool-RESULT
    fields on the OUTPUT payload, scoped by actor, output-only, suppressed under `monitor`, audited via
    `redacted_fields`. Closes finding #1 (field-RBAC absent on adapter path; HTTP path already masked). Files
    policy_engine.py + mcp_scan_orchestrator.py + mcp_proxy.py + test_mcp_scan_orchestrator.py (+7 tests, 22
    passed, 1056 broad sweep). Backward-compat: redaction_fields=[] → no-op. REMAINING: cross-stage
    (input-triggered) parity + live drive → item 3b stays open (advanced).
  - CHG-0025 (2026-07-02) — G2 item 3b COMPLETE ([x]): cross-stage input-triggered field projection. The RBAC
    "role X never sees field F" pattern authors the rule on the CALL, so the INPUT-stage policy match must
    project fields out of the RESPONSE (control HTTP-path parity). Gateway now: input scan surfaces
    `policy_redaction_fields` in meta; `org_mcp_jsonrpc` threads `_in_rfields` into both adapter OUTPUT scans
    as `extra_redaction_fields`; `apply_field_redaction` returns identity on a no-op; the adapter swap gate
    also fires on `redacted_fields` so a finding-less projection isn't discarded. Files policy_engine.py +
    mcp_scan_orchestrator.py + mcp_proxy.py + test_mcp_scan_orchestrator.py (+5) + test_e12_result_redaction.py
    (+2 end-to-end). Gate: 37 relevant + 1063 broad sweep passed. Both trigger directions (output-content +
    input-call) now covered on the adapter path. RESIDUAL (non-blocking): bare-REST cross-stage + optional
    live drive.
  - CHG-0026 (2026-07-02) — G3 item 7 [x]: migrated the websocket transport onto the sandbox broker path.
    `_adapter_forward` now routes ws via `broker_send_rpc` (transport='websocket') alongside streamable-http/
    sse; the in-gateway `mcp_ws_adapter.send_jsonrpc` dial is removed. ws was the last transport still opening
    an upstream socket from inside the gateway process, despite `_is_sandbox_routed` already declaring it
    sandbox-routed and the broker/agent already supporting ws upstreams. Now ALL four transports (stdio +
    streamable-http + sse + websocket) egress via the per-org sandbox by default — gateway never dials
    upstream. Files mcp_proxy.py + test_mcp_http_via_sandbox.py (+1: asserts broker routing + in-gateway ws
    NOT dialed). Gate: 1064 gateway + 52 broker (ws/upstream/route/rpc/lifecycle) passed. Caveat: http/sse
    still honor MCP_HTTP_VIA_SANDBOX (default ON); stdio+ws unconditional. mcp_ws_adapter now legacy (benign
    main.py reaper/shutdown no-ops remain).
  - CHG-0027 (2026-07-02) — G4 item 13 (partial): added a docker healthcheck + restart to the gateway service
    (the ONLY core service with neither; CHG-0020 flagged health=none → never auto-restarted). Probes the
    auth-exempt /health (200 ok / 503 on signing misconfig) on both docker-compose.yml (base: healthcheck +
    restart: unless-stopped) and docker-compose.prod.yml (prod: healthcheck; restart via anchor). Config-only
    (running container NOT recreated). VERIFY: `docker compose ... config` renders gateway.healthcheck +
    restart=unless-stopped; both merged configs parse. Item 13 STILL OPEN: OTEL tracing + PG/Redis backup
    remain; optional peer service_healthy upgrade.
  - CHG-0028 (2026-07-02) — G5 item 20 (advance): extended mcp_live_matrix_harness.py with a per-actor
    AUTHZ-under-load oracle. The harness proved REDACTION under load but had no authz dimension. Added
    authz_denied() (403 / authz-error / [BLOCKED] result; a generic error is NOT a denial) + authz_violation()
    (True only when a forbidden tool EXECUTED successfully under load) + a Scenario type + a DENY_TOOL_NAME-
    gated F_authz_deny concurrent agent; the gate now FAILS on any authz violation and flags a vacuous deny
    run. Files scripts/mcp_live_matrix_harness.py + scripts/test_mcp_live_matrix_oracle.py (+3 oracle tests →
    8 passed). Backward-compat: unset DENY_TOOL_NAME → 5-agent redaction matrix unchanged. REMAINING: live
    peak run (5k-10k, item 15) with a real denied tool + tag-enforcement MCPEvent audit under load.
  - CHG-0029 (2026-07-02) — G5 harness quality: hardened the mcp_pipeline_matrix_live.py oracle. It was
    leak-blind (pii = _SSN in text — ONE hardcoded SSN in ONLY result.content[0].text, so a redact-but-forward
    in a later content item / structuredContent / nested field / any non-SSN value passed as redacted) and
    import-unsafe (KEY/argv/asyncio.run at module scope). Now find_pii_in_body() scans the FULL serialized
    response for the case's ACTUAL sensitive values (case_sensitive_values: explicit case['pii'] or SSN
    fallback); redacted = allow + no raw value anywhere + a marker (byte-truth); module import-safe. +8 unit
    tests (scripts/test_mcp_pipeline_oracle.py); all 4 scripts oracle suites → 25 passed. Same class of fix as
    CHG-0009 (dead oracle) + CHG-0012 (no byte-check).
  - CHG-0030 (2026-07-02) — 1.4 "PII/IP/regulated": extended MCP compliance tagging to IP/infrastructure
    leakage. detect_ip_leakage + IP_LEAKAGE_PATTERNS (internal IPv4/hostname/URL + private file paths → INFRA)
    already ran on the chat output_guard but the MCP scan (_scan_text_tier1) ran ONLY detect_pii/detect_secrets
    — so an internal host/IP/path in a tool RESULT was never detected/tagged/redacted. Now folded in: an
    ip_leakage finding is INFRA-tagged (via get_compliance_tags) + enforced by posture (block→block,
    redact→redact_all, monitor→tag). Fail-closed byte-check: redact_all masks internal IP/host/URL but NOT file
    paths, so if a detected internal value survives the scrub under redact it BLOCKS (no redact-that-leaks).
    Public IPs not flagged. Files mcp_scan_orchestrator.py + test_mcp_scan_orchestrator.py (+5). Gate: 32 +
    1069 broad sweep passed. (Distinct from item 5's tag-vocab mismatch, still open.)
  - CHG-0031 (2026-07-02) — G3 item 9 (gateway rate-limit parity): the bare REST route org_mcp_tool_call
    enforced the per-KEY tool-call cap but NOT the per-ORG TPM/burst/RPM rate limit that org_mcp_jsonrpc
    applies — a tenant could exceed org ceilings via /tools/call. Extracted _mcp_org_rate_limit_raw (plain 429);
    the JSON-RPC route wraps it (unchanged), the bare route returns it as-is. Files mcp_proxy.py +
    test_mcp_rate_limit.py (+3). Gate: 7 rate-limit + 1072 broad sweep passed. Same bare-route parity class as
    CHG-0006. REMAINING (item 9): live threshold probe + adversarial policy + audit-completeness; ext_mcp_proxy
    also lacks the per-org limiter (follow-up if tenant-exposed).
  - CHG-0032 (2026-07-02) — G3 item 9: closed the ext_mcp_proxy rate-limit follow-up. The authenticated
    external MCP proxy (/v1/mcp/ext-proxy/{host}/{path}, behind auth middleware, NOT in EXCLUDED_PATHS) had
    inbound credential + SSE result scanning but NO per-org rate limit — a tenant could drive it past org
    ceilings. Added _mcp_org_rate_limit_raw(_get_auth_context(request)) after the domain allowlist check
    (plain 429 before scan/forward). Now ALL THREE tenant-facing MCP entry points (org_mcp_jsonrpc,
    org_mcp_tool_call, ext_mcp_proxy) enforce the per-org TPM/burst/RPM ceiling. Files mcp_proxy.py +
    test_mcp_rate_limit.py (+3). Gate: 10 rate-limit + 1075 broad sweep passed. Code-level rate-limit coverage
    complete; item 9 REMAINING: live threshold probe + adversarial policy + audit-completeness.
  - CHG-0033 (2026-07-02, HIGH) — 1.4 least-privilege/credential leak: ext_mcp_proxy forwarded the caller's
    request headers verbatim (only host/content-length/transfer-encoding stripped) to the third-party external
    MCP server — so the caller's Authorization: Bearer <gateway-API-key>, Cookie, and X-Api-Key egressed to the
    external domain (replayable against the gateway). The sandbox path already built a clean header set. Fix:
    new _ext_proxy_forward_headers strips hop-by-hop + credential/identity headers (authorization/cookie/
    x-api-key/x-gateway-*) and injects ONLY the upstream's own stored OAuth token (if any) as Authorization.
    Files mcp_proxy.py + test_mcp_bare_proxy_scan.py (+2). Gate: 20 bare-proxy + 1077 broad sweep passed.
  - CHG-0034 (2026-07-02) — G3 item 9 (gateway validation/DoS): the MCP routes buffered the whole body
    (request.body()/json()) with NO size ceiling (RAG/embeddings already 413-guard). Added _MCP_MAX_BODY_BYTES
    (default 10MiB, env MCP_MAX_BODY_BYTES) + _mcp_body_too_large() → 413 mcp_body_too_large on an oversized
    Content-Length, BEFORE buffering, on org_mcp_jsonrpc + org_mcp_tool_call + ext_mcp_proxy. Non-invasive
    (Content-Length pre-check, body-read flow untouched). Files mcp_proxy.py + test_mcp_rate_limit.py (+4).
    Gate: 14 + 1081 broad sweep passed. Documented limitation: doesn't catch chunked-without-Content-Length
    (infra body limit covers it; app-layer streaming cap deferred to avoid test-harness churn).
  - CHG-0035 (2026-07-02, verification) — read-only audit triggered by CHG-0033: checked the sibling
    security-critical egress/isolation paths for the same leak class. All CLEAN. Sandbox agent HTTP:
    follow_redirects=False + no verify=False anywhere (TLS verify default-on) + timeouts. WS: ws/wss only,
    default verifying SSL context. Container (docker_manager): no-new-privileges + DEFAULT seccomp (not
    unconfined) + cap_drop=ALL + read_only rootfs + memswap_limit=mem (swap off) + pids/mem/cpu limits.
    Multi-org cross-tenant harness: byte-level canary oracle + fail-closed negative matrix. Evidence
    mcp-parallel/findings/backstop-p12b-sandbox-egress-hygiene/audit.md. No code changed. RESIDUAL (item 12):
    runc (not gVisor) + open per-org NAT — infra, not code.

## Ralph autonomous loop — gateway hardening
- Backlog + status live in scripts/ralph/prd.json; learnings in scripts/ralph/progress.txt.
- ONE story per iteration; never break a passing gate; never mark passes:true without a green gate.
- Backend gate: `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests -q` (+ `./.venv/bin/python
  -m pytest tests/leakhunt` for leaks — build that suite under gateway/tests/leakhunt; +
  test_openai_sdk_compat.py for SDK). Frontend gate: `cd frontend && npm run lint && npm run build`
  plus dev-browser verification for UI stories.
- Security invariants are non-negotiable: egress bytes are the only source of truth; any redact/block
  verdict or assume_redacted attestation must be byte-verified; fail closed on a no-op scrub.
- Reuse responses_adapters.py error helpers; respect patterns.py false-positive protection; mirror the
  output-guard honesty check onto the input/egress path.
- UI-story gate (D*): the durable re-runnable gates are `scripts/playwright_*.mjs`. Run with
  `NODE_PATH="$PWD/tests/e2e/node_modules" BASE_URL=http://127.0.0.1:8180 node scripts/playwright_<story>.mjs`
  — playwright lives in `tests/e2e/node_modules`, NOT `frontend/`. They drive the LIVE docker stack
  (Vite :8180 → control :8100 → gateway :8300); login admin@zeroshield.io / Adm1n!Pass#2024 via the REAL
  form (localStorage-token injection alone does NOT establish a session — the AuthContext guard bounces to
  /login). Module-1.1 is telemetry-heavy: keep panel/login waits ≥60s so a slow-but-correct render is not a
  false failure; never lower an assertion to make a flaky gate pass.
