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
