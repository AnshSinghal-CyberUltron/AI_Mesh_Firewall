# MCP Hardening Changelog — canonical four-memory log

Canonical, human-readable ledger for the **MCP-hardening BACKSTOP** effort (Claude Code + Cursor
sessions running in parallel). This file is memory #4 of four. Every hardening change MUST be logged
to **all four** memories in the same commit:

1. **Ruflo** — `mcp__ruflo__memory_store(namespace="mcp-hardening/changes", key="<change-id>", value=…)`
   followed by `mcp__ruflo__hooks_notify(...)` so sibling sessions see it live.
2. **Claude** — a one-line pointer under *MCP Hardening BACKSTOP changelog* in the repo-root `AGENTS.md`.
3. **Cursor** — an entry in `.cursor/rules/mcp-hardening-changelog.mdc`.
4. **This file** (`docs/mcp/HARDENING_CHANGELOG.md`) — the full, canonical entry (§Entries below).

## §0 — Protocol (READ FIRST)

Rationale: multiple autonomous sessions edit the same worktree/index concurrently
(see `~/.claude/.../memory/gateway-shared-index-hazard.md`). Without a single traceable ledger, a
backstop edit that corrects another session's omission is invisible and can be silently reverted or
duplicated. This protocol makes every change attributable, reversible, and verifiable.

Rules:
- **One change-id per logical change.** Format: `CHG-NNNN` (zero-padded, monotonic). Never reuse an id.
- **Log in the SAME commit as the change.** A code change without its four-memory entry is incomplete.
- **Every entry carries, at minimum:** change-id · date · files · WHAT changed · WHY (which
  gap/mistake/omission) · what it NOW DOES · whose work it touched · how to VERIFY.
- **Commit small; rebase from `main` often.** Stage narrowly (never `git add -A`) — the index is shared.
- **Evidence wins.** A "redact/block" verdict is valid ONLY if the captured egress bytes reflect it;
  fail closed on a no-op scrub. Cross-check leak claims with an independent oracle
  (`mcp__ruflo__aidefence_scan` / `aidefence_has_pii`) over the actual bytes.
- **Gates before any scratchpad `[x]`:** relevant pytest + broker tests + the item's stress harness
  green; frontend → build + Playwright verified.

### Entry template (copy for each new change)

```
### CHG-NNNN — <short title>
- **Date:** YYYY-MM-DD
- **Scratchpad item:** G<n> item <n> (scripts/ralph/mcp_hardening_scratchpad.md)
- **Files:** path:line …
- **WHAT:** <the concrete change>
- **WHY (gap/mistake/omission):** <root cause / what was missing or wrong>
- **NOW DOES:** <post-change behavior / invariant now enforced>
- **Touched whose work:** <backstop-only | corrects <session/commit> | extends <session/commit>>
- **VERIFY:** <exact commands / harness / oracle to reproduce green>
```

## §Entries

### CHG-0001 — Establish the four-memory hardening changelog protocol
- **Date:** 2026-07-02
- **Scratchpad item:** G0 item 0 (four-memory changelog + runsc enforcement audit)
- **Files:** `docs/mcp/HARDENING_CHANGELOG.md` (new) · `.cursor/rules/mcp-hardening-changelog.mdc` (new)
  · `AGENTS.md` (new *MCP Hardening BACKSTOP changelog* section) · `scripts/ralph/mcp_hardening_scratchpad.md` (item 0 marked).
- **WHAT:** Created the canonical changelog + §0 protocol + entry template, the Cursor rule mirror, and
  the Claude/AGENTS.md pointer. Bootstraps the traceability discipline the whole BACKSTOP effort requires.
- **WHY (gap/mistake/omission):** None of the four changelog memories existed
  (`HARDENING_CHANGELOG.md`, `BACKSTOP_FINDINGS.md`, `.cursor/rules/mcp-hardening-changelog.mdc` all
  absent). Parallel sessions had no shared, attributable ledger, so a backstop correction of another
  session's omission would be invisible and could be reverted/duplicated.
- **NOW DOES:** Every subsequent hardening change has one place to record change-id + files + what/why +
  verification, mirrored to Ruflo, Claude, and Cursor memories in the same commit.
- **Touched whose work:** backstop-only (net-new docs + protocol; no code paths altered).
- **VERIFY:**
  - Files exist: `ls docs/mcp/HARDENING_CHANGELOG.md .cursor/rules/mcp-hardening-changelog.mdc`
  - AGENTS.md pointer: `grep -n "MCP Hardening BACKSTOP changelog" AGENTS.md`
  - Ruflo entry: `mcp__ruflo__memory_search(query="CHG-0001", namespace="mcp-hardening/changes")`
  - Runsc enforcement audited fail-closed (see note below): `services/mcp-broker/src/sandbox/docker_manager.py:343-359`.

**Item-0 runsc audit (no code change needed — mechanism already present & correct):**
`DockerSandboxManager._resolve_runtime()` (`docker_manager.py:343`) raises `SandboxRuntimeUnavailableError`
(fail-closed) when `MCP_SANDBOX_RUNTIME_REQUIRED=true` and either `MCP_SANDBOX_RUNTIME` is unset OR the
named runtime (e.g. `runsc`) is not reported by `docker info`.`Runtimes`. Production deployments MUST set
`MCP_SANDBOX_RUNTIME=runsc` **and** `MCP_SANDBOX_RUNTIME_REQUIRED=true`; with both set, a host missing
gVisor refuses to launch sandboxes rather than silently downgrading to the default runc runtime. This is
recorded here as the item-0 enforcement contract; verification of the *runtime-required* env being set in
the prod compose/manifests is tracked under G3 item 12.

### CHG-0002 — Backstop audit → ranked findings (BACKSTOP_FINDINGS.md)
- **Date:** 2026-07-02
- **Scratchpad item:** G1 item 1 (backstop audit of parallel Claude+Cursor sessions)
- **Files:** `docs/mcp/BACKSTOP_FINDINGS.md` (new) · `scripts/ralph/mcp_hardening_scratchpad.md` (item 1 [x] + priority order).
- **WHAT:** Ran a read-only 6-auditor parallel workflow (`wf_ca0d6349-33e`, 7 agents, 904k tokens) over the
  gateway guardrail chain, control-plane compliance/authz, broker sandbox, stress harnesses, and frontend;
  synthesized/deduped to 24 findings (13 high · 8 medium · 1 low), each grounded in file:line + a verify command.
- **WHY (gap/mistake/omission):** No consolidated, evidence-based map of what the parallel sessions left
  incomplete/wrong existed; items 2–20 had no prioritized, verifiable roadmap.
- **NOW DOES:** Gives the backstop a ranked, corroboration-annotated worklist. Key confirmed gaps: SSE
  `ext_mcp_proxy` egress unscanned (raw PII/secret today); per-actor authz absent on stdio/ws adapter path;
  compliance tags are audit-only (no tag-driven enforcement) with two disjoint vocabularies; gVisor/egress
  default fail-open; only stdio reaches the sandbox; a **dead cross-tenant oracle** (`for fs in []` →
  `foreign_org_events` structurally 0) and a **15-sandbox scale ceiling** (hardcoded 3 orgs) that overstate
  prior "isolation/scale validated" claims.
- **Touched whose work:** backstop-only (net-new audit doc). Findings implicate prior sessions' commits
  (e.g. 5d0dd346 "scale matrix validated" was 6/6 403s at 15 MCPs) but no code was changed.
- **VERIFY:** Spot-verified 3 load-bearing claims directly: `sed -n '1021,1035p' gateway/ai_mesh_gateway/mcp_proxy.py`
  (SSE `aiter_bytes` passthrough); `python3 -c "print(any(1 for fs in []))"` → False +
  `sed -n '129p' scripts/mcp_scale_matrix_live.py`; `grep -n 'ORGS = \[' -A5 scripts/mcp_scale_provision.py`
  (literal 3-tuple). Full doc: `docs/mcp/BACKSTOP_FINDINGS.md`. Ruflo: `memory_search CHG-0002 namespace mcp-hardening/changes`.

### CHG-0003 — Fail-CLOSED result-scan error path (G2 item 2, partial)
- **Date:** 2026-07-02
- **Scratchpad item:** G2 item 2 (field-level redaction of RESULTS, byte-verified, fail-closed) — PARTIAL.
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`_scan_tool_result_floor`, ~690-780) ·
  `gateway/ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py` (+2 tests).
- **WHAT:** Made `_scan_tool_result_floor` fail **closed** on a scan exception, in BOTH the primary output
  scan (was `return result_content, False, …, {"result_scan_error"}` → RAW) and the redaction-floor
  re-scan (was `log + fall through` → RAW after PII was already detected). Both now return
  `blocked=True` + `["SCAN_ERROR"]` tag + `result_scan_failclosed` meta.
- **WHY (gap):** BACKSTOP_FINDINGS G2 item 2 — the outbound result floor was fail-OPEN while its inbound
  twin `_scan_tool_args_block` fails closed (`arg_scan_error`). A scanner hiccup (or a masking-rescan
  error after PII was detected) silently egressed the RAW tool RESULT — a data leak.
- **NOW DOES:** All three bare routes (`org_mcp_tool_call`, `internal_tools_call`, `ext_mcp_proxy`
  non-streaming) already map `blocked=True` → a graceful JSON-RPC/HTTP block error + `decision="block"`
  audit event; the un-inspectable result is WITHHELD, never forwarded. Graceful block (not 500) — the
  transport stays up; availability yields to confidentiality.
- **Touched whose work:** extends the shared mcp_proxy scan chain (built across many prior sessions); no
  other session's in-flight code altered.
- **VERIFY:** `cd gateway && PYTHONPATH="$PWD/../shared:$PWD/ai_mesh_gateway" ./.venv/bin/python -m pytest
  ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py -q` → 10 passed. The 2 new tests
  (`test_rest_result_scan_error_fails_closed`, `test_ext_result_scan_error_fails_closed`) patch
  `_mcp_security_scan` to raise ONLY on `scan_direction="output"` and byte-assert the raw PII is ABSENT +
  `blocked` — an independent check that does NOT rely on the scanner under test. Broad sweep
  (`-k "mcp or scan or redact or e12 or result or floor or bare or proxy"`) → 323 passed / 18 skipped.
- **REMAINING for G2 item 2 (next iterations):** (1) SSE `ext_mcp_proxy` still passes tool RESULTS raw
  (`streaming_egress_unscanned`) — needs buffer-and-scan like `internal_tools_call`; (2) non-streaming
  result shapes other than dict `result.content` (plain string / `structuredContent`) are not scanned;
  (3) audit the main `org_mcp_jsonrpc` inline result path (~2265/2451) for the same fail-open on scan error.

### CHG-0004 — SSE tool-RESULT buffer-and-scan on ext_mcp_proxy (G2 item 2, closes the HIGH leak)
- **Date:** 2026-07-02
- **Scratchpad item:** G2 item 2 (field-level redaction of RESULTS, byte-verified, fail-closed) — advances it.
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`ext_mcp_proxy` SSE branch; new module helper
  `_scan_reframe_sse_tool_result`; `_ext_is_tools_call` flag) ·
  `gateway/ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py` (replaced the leak-pinning streaming test
  with 3 SSE tests).
- **WHAT:** `ext_mcp_proxy` now BUFFERS a finite `tools/call` SSE (`text/event-stream`) response, runs the
  outbound result floor on every `data:` frame's `result.content`, and re-emits as SSE (masked) — or
  returns a JSON-RPC block error. Non-`tools/call` SSE (notifications / long-lived) still passes through
  live (no tool result to scan; buffering could hang).
- **WHY (gap):** BACKSTOP_FINDINGS G2 item 2 **HIGH** — SSE is the DEFAULT MCP Streamable-HTTP result mode
  and the branch forwarded `resp.aiter_bytes()` verbatim with only a `streaming_egress_unscanned` warning,
  so raw PII/secret tool RESULTS egressed. The repo's own `test_ext_streaming_egress_unscanned_but_flagged`
  pinned the leak.
- **NOW DOES:** PII/secret in an SSE tool result is masked or blocked (fail-closed via the CHG-0003 floor);
  the raw result never egresses on the SSE path. SSE framing + content-type preserved for the client.
- **Touched whose work:** extends `ext_mcp_proxy` (built across prior sessions); replaced the test that
  asserted the old leak. No other in-flight code altered.
- **VERIFY:** `cd gateway && PYTHONPATH="$PWD/../shared:$PWD/ai_mesh_gateway" ./.venv/bin/python -m pytest
  ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py -q` → 12 passed
  (`test_ext_sse_tool_result_redacted`, `test_ext_sse_result_scan_error_fails_closed`,
  `test_ext_non_toolscall_sse_passthrough`). Broad sweep → 342 passed / 18 skipped. **Independent leak
  oracle** over the captured reframed egress bytes: `aidefence_has_pii(raw)` = true (oracle works),
  `aidefence_has_pii(masked SSE egress)` = false — the egress is verified clean by a detector other than
  the scanner under test.
- **REMAINING for G2 item 2:** (2) plain-string / `structuredContent` result shapes in the ext
  non-streaming branch; (3) audit the main `org_mcp_jsonrpc` inline result path for the same fail-open.

### CHG-0005 — Whole-`result` scan (all shapes) + main-path fail-open audit — CLOSES G2 item 2
- **Date:** 2026-07-02
- **Scratchpad item:** G2 item 2 (field-level redaction of RESULTS, byte-verified, fail-closed) — COMPLETE.
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`ext_mcp_proxy` non-streaming branch;
  `_scan_reframe_sse_tool_result`) · `gateway/ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py` (+2 tests).
- **WHAT:** Broadened the ext-proxy result scan (both the non-streaming JSON branch AND the SSE reframe
  helper) to scan the ENTIRE `result` — dict `content`/`structuredContent`, a bare list, or a plain
  string — instead of only dict `result.content`. Also **audited the main `org_mcp_jsonrpc` paths** for
  the CHG-0003 fail-open class.
- **WHY (gap):** BACKSTOP_FINDINGS G2 item 2 — a result under `structuredContent`, or a plain-string
  result, egressed UNSCANNED on the ext path (the condition required `isinstance(result, dict) and
  result.content is not None`). My own CHG-0004 SSE helper inherited the same `content`-only limitation.
- **NOW DOES:** Every ext-proxy result shape is scanned/redacted (parity with the org path, which already
  scanned the whole `result`). `_scan_tool_result_floor` recursively walks dict/list/str and fails CLOSED
  on scan error.
- **(c) AUDIT RESULT — main path is fail-SAFE, NOT fail-open (no fix needed):** the adapter branch
  (`if is_adapter_transport`, line ~2332) is a plain `if`, and the http/sse branch's `try` (~2468) has
  ONLY `except httpx.TimeoutException`/`except httpx.RequestError` — no generic `except`. So a scanner
  exception at the inline `_mcp_security_scan` (adapter ~2367, http/sse ~2528) PROPAGATES → HTTP 500;
  the raw upstream `data` is returned only AFTER a successful scan (~2647), so it is never egressed on
  scan error. The CHG-0003 fail-OPEN was specific to the bare routes' shared helper, which explicitly
  caught the exception and returned raw. (Converting the main-path 500 into a graceful block is a
  deferred AVAILABILITY enhancement — not a leak.)
- **Touched whose work:** extends `ext_mcp_proxy` (prior sessions); backstop-only audit of the main path.
- **VERIFY:** `cd gateway && PYTHONPATH="$PWD/../shared:$PWD/ai_mesh_gateway" ./.venv/bin/python -m pytest
  ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py -q` → 14 passed
  (`test_ext_redacts_pii_in_structured_content`, `test_ext_redacts_pii_in_string_result`). Broad sweep
  (`-k "mcp or scan or redact or e12 or result or floor or bare or proxy or sse or adapter or jsonrpc"`)
  → 362 passed / 18 skipped. Audit evidence: `grep -n "except httpx" gateway/ai_mesh_gateway/mcp_proxy.py`
  (only Timeout/RequestError on the http/sse try; no generic except).
- **G2 item 2 STATUS:** COMPLETE — result redaction is byte + independent-oracle verified across all bare
  routes (rest / internal / ext streaming + non-streaming, all result shapes), fail-CLOSED on the bare
  routes and fail-SAFE (500) on the main path. Deferred (NOT leaks): main-path graceful-block vs 500;
  per-actor FIELD-level RBAC masking is item 3.

### CHG-0006 — Per-key authorization parity on the bare REST route (G2 item 3, finding #2)
- **Date:** 2026-07-02
- **Scratchpad item:** G2 item 3 (per-user/agent/role tool authorization) — PARTIAL (closes finding #2).
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`org_mcp_tool_call`) ·
  `gateway/ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py` (extended `_auth`; +4 tests).
- **WHAT:** Added the three per-key authorization gates to `org_mcp_tool_call`, run BEFORE the arg scan /
  forward: `_tool_allowed_by_key` (→ 403), the `mcp_max_tool_calls` per-turn cap via
  `_incr_tool_call_count` + `_tool_call_cap_exceeded` (→ 429), and `_is_tool_disabled` (→ 403).
- **WHY (gap):** BACKSTOP_FINDINGS G2 item 3 finding #2 — the bare REST `tools/call` route enforced NONE
  of the key controls that `org_mcp_jsonrpc` enforces (`mcp_allowed_tools`/`mcp_max_tool_calls` are
  gateway-only, not re-checked by the backend), so a caller could invoke a tool outside its key allowlist,
  exceed the per-turn cap, or call a disabled tool via `POST /gateway/{org}/mcp/{server}/tools/call`.
- **NOW DOES:** The REST route blocks disallowed / over-cap / disabled tool calls before forwarding, at
  parity with the JSON-RPC route; each records a `decision="block"` gateway audit event.
- **Touched whose work:** extends `org_mcp_tool_call` (prior sessions); reuses the existing gate helpers.
- **VERIFY:** `cd gateway && PYTHONPATH="$PWD/../shared:$PWD/ai_mesh_gateway" ./.venv/bin/python -m pytest
  ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py -q` → 18 passed
  (`test_rest_blocks_tool_not_in_key_allowlist` [403 + backend `post` not awaited],
  `test_rest_blocks_over_tool_call_cap` [429], `test_rest_blocks_disabled_tool` [403],
  `test_rest_allowed_tool_in_allowlist_passes`). Broad sweep
  (`-k "mcp or scan or redact or proxy or jsonrpc or rate or auth or key or tool"`) → 424 passed / 18 skipped.
- **REMAINING for G2 item 3:** finding #1 (the big one) — per-actor (user/agent/role) tool authorization +
  field-level RBAC masking are NOT enforced on the stdio/websocket ADAPTER path (`actor` is threaded for
  scan attribution but never used for an access decision; the enabled-tools payload carries no actor
  dimension); finding #3 — Tier-1/2 policy BLOCK gated on posture not the rule's action (actor-scoped
  `block` downgraded to `tag`); finding #4 — `_policy_applies_to_actor` allowlist-scope inverts intent.

### CHG-0007 — Honor rule's `block` action under non-block posture + finding #4 is a FALSE POSITIVE (G2 item 3, #3/#4)
- **Date:** 2026-07-02
- **Scratchpad item:** G2 item 3 (per-user/agent/role tool authorization) — PARTIAL (closes #3; #4 dismissed).
- **Files:** `gateway/ai_mesh_gateway/mcp_scan_orchestrator.py` (`_scan_text_tier1`) ·
  `gateway/ai_mesh_gateway/tests/test_mcp_scan_orchestrator.py` (+2 tests).
- **WHAT (finding #3, FIXED):** Tier-1 policy evaluation now sets `blocked=True` when a matched rule's own
  `eval_result.action == "block"` under ANY non-`monitor` posture — not only when the coarse posture is
  `block`. The block-posture FLOOR (blocks any matched rule) is preserved.
- **WHY (#3):** A rule authored `action='block'` was downgraded to detect-and-tag under the default `tag`
  posture on the gateway path. The stdio/websocket ADAPTER path bypasses the backend that re-enforces the
  rule, so an actor-scoped block rule silently didn't block there — inconsistent with the control-plane
  engine (`control/.../policy/engine.py` blocks on `result.action == "block"`) and the HTTP path.
- **NOW DOES (#3):** An actor-scoped block rule blocks on the adapter path under any non-monitor posture,
  at parity with HTTP; `monitor` (explicit observe-only) still wins.
- **FINDING #4 — FALSE POSITIVE (no code change; Devil's-Advocate correction):** the audit claimed
  `_policy_applies_to_actor` "inverts intent" (a block policy scoped `allowed_roles=['admin']` blocks
  admins, allows others). Verified this is **correct-as-designed**: it is a policy-SCOPING primitive
  (`allowed_*` = "the actors this policy APPLIES to", documented at `policy_engine.py:171-193`) that
  deliberately MIRRORS the control-plane engine (line 173) with a fail-closed rationale. "This block
  targets admins" is coherent scoping, not an inversion. Inverting the logic would break the documented
  contract, control-plane parity, and existing policies. The auditor conflated policy-scoping with the
  ABSENT deny-by-default per-actor tool-authz primitive (which IS the real gap — finding #1). **Do not
  "fix" `_policy_applies_to_actor`.**
- **Touched whose work:** extends the two-tier scan orchestrator (many prior sessions); backstop-only
  correction of the CHG-0002 audit re #4.
- **VERIFY:** `cd gateway && PYTHONPATH="$PWD/../shared:$PWD/ai_mesh_gateway" ./.venv/bin/python -m pytest
  ai_mesh_gateway/tests/test_mcp_scan_orchestrator.py -q` → 15 passed
  (`test_block_rule_honored_under_tag_posture` [tag→blocked],
  `test_block_rule_not_honored_under_monitor_posture` [monitor→not blocked];
  `test_redact_rule_still_redacts_under_redact_enforcement` still green). Broad sweep
  (`-k "mcp or scan or redact or policy or orchestrator or proxy or tier or block"`) → 427 passed / 18 skipped.
- **REMAINING for G2 item 3:** finding #1 — per-actor (user/agent/role) tool authorization + field-level
  RBAC masking on the stdio/websocket adapter path (the enabled-tools payload needs an actor dimension, or
  the adapter path must route through actor-scoped policy eval). Tier-2 posture-gating of a `verdict.action
  == "block"` is a separate LLM-judge semantic (deferred, not an authored-rule authz concern).

### CHG-0008 — Per-actor ACCESS authz is enforced on the adapter path (finding #1 corrected) — CLOSES G2 item 3 authz
- **Date:** 2026-07-02
- **Scratchpad item:** G2 item 3 (per-user/agent/role tool authorization) — authorization COMPLETE; a
  narrower field-redaction follow-up is split out as item 3b.
- **Files:** `gateway/ai_mesh_gateway/tests/test_mcp_scan_orchestrator.py` (+1 end-to-end test).
- **WHAT:** Added `test_scan_enforces_actor_scoped_block_on_adapter_path` — an end-to-end proof (REAL
  compiled bundle, no mocked evaluate) that `scan_mcp_payload` (the path the stdio/ws ADAPTER uses via
  `_mcp_security_scan(actor=...)`) enforces a per-ROLE block policy under the DEFAULT `tag` posture:
  blocks the scoped role, allows a different role.
- **WHY (finding #1 — CORRECTED):** the audit claimed "actor is never used for an access decision on the
  adapter path." VERIFIED IMPRECISE: per-actor ACCESS authz (block/allow by user/agent/role) IS enforced
  via `evaluate_mcp_policies(actor=...)` + `_policy_applies_to_actor` — already unit-tested in
  `test_policy_engine_actor_scoping.py` — which the adapter path invokes through the scan orchestrator;
  CHG-0007 added rule-block honoring under the default posture, and this change proves the full chain
  end-to-end. The `mcp_proxy.py:301-307` "cache key" gap is a DOCUMENTED non-issue (tool enable/disable is
  server-scoped by design; per-actor authz lives in the per-call policy layer, correctly actor-keyed, not
  in the enabled-tools cache).
- **NOW DOES:** per-actor tool ACCESS authorization (block/allow by user/agent/role) is enforced + tested
  across the HTTP path (`MCPToolCallView`), the stdio/ws ADAPTER path (scan orchestrator), and the bare
  REST route's per-key controls (CHG-0006).
- **Touched whose work:** backstop-only (net-new test + audit correction). No product code changed this entry.
- **VERIFY:** `cd gateway && PYTHONPATH="$PWD/../shared:$PWD/ai_mesh_gateway" ./.venv/bin/python -m pytest
  ai_mesh_gateway/tests/test_mcp_scan_orchestrator.py ai_mesh_gateway/tests/test_policy_engine_actor_scoping.py -q`
  → 27 passed.
- **SPLIT-OUT as item 3b (the genuine remaining gap):** per-policy FIELD-level redaction (`redaction_fields`
  → `apply_field_redaction`/`redact_structured`, control `views.py:1113`) masks specific NAMED result
  fields for matched actor-scoped policies on the HTTP path only. The gateway policy engine/bundle has NO
  field-redaction support (only `redaction_hints`), so the stdio/ws adapter path does content-scan but not
  field-level RBAC masking. Closing it needs a bundle-format extension (add `redaction_fields` to compiled
  policies + gateway `EvaluationResult` + apply on the adapter response) — a scoped cross-cutting follow-up.

### CHG-0009 — Real, gated, unit-tested cross-tenant audit-log leakage oracle (G5 item 19, oracle fixed)
- **Date:** 2026-07-02
- **Scratchpad item:** G5 item 19 (cross-tenant leakage canaries) — the ORACLE is fixed; the live-at-scale
  proof remains.
- **Files:** `scripts/mcp_scale_matrix_live.py` · `scripts/test_mcp_scale_oracle.py` (new).
- **WHAT:** Replaced the dead cross-tenant oracle `foreign = sum(1 for r in rows if any(... for fs in []))`
  (an empty `for fs in []` → `any(...)` always False → structurally 0) with a real, pure, unit-tested
  `count_foreign_events(rows, other_slugs)`; **added it to the PASS gate** (`total_foreign == 0` — it was
  computed but NOT gated before); made the module import-safe (manifest load wrapped so the pure oracle is
  importable/testable without a live manifest); renamed the misleading `total_egress_bytes` →
  `request_payload_bytes` (it measures the REQUEST size we generate, not on-the-wire egress — the audit's
  recommendation, no code consumer) and added `foreign_org_events_total` to the report.
- **WHY (mistake):** BACKSTOP_FINDINGS G5 item 19 — the oracle was a **fabricated metric** that could never
  trip regardless of real audit-row leakage, AND it was not part of the PASS gate, so the harness gave a
  false "audit isolation proven" signal. `total_egress_bytes` similarly implied a leak-proof it didn't provide.
- **NOW DOES:** SCALE MATRIX PASS additionally asserts **zero cross-tenant audit-log leakage** via a real
  predicate (a row is foreign if its `org_slug` is another tenant's OR its content references
  `/mcp/<other_slug>`); the oracle is unit-tested including a case proving the OLD predicate missed a real leak.
- **Touched whose work:** the scale harness (prior sessions — the "scale matrix validated"/P8-P9 commits
  whose isolation evidence rested on this metric).
- **VERIFY:** `gateway/.venv/bin/python scripts/test_mcp_scale_oracle.py` → `ALL 5 ORACLE TESTS PASSED`
  (incl. `test_old_dead_predicate_missed_the_leak`). Harness still imports:
  `gateway/.venv/bin/python -c "import sys;sys.path.insert(0,'scripts');import mcp_scale_matrix_live"`.
- **REMAINING for G5 item 19:** run the leakage-canary matrix LIVE at 500-sandbox scale under chaos with
  this corrected+gated oracle, and capture REAL sandbox egress bytes cross-checked with an independent
  `aidefence` oracle — tied to item 14 (true 300–500-sandbox scale, still a hardcoded-3-org ceiling). This
  entry removes the fabricated metric; the live-at-scale proof is separate.

### CHG-0010 — Scale-provisioner org count is configurable (removes the 15-sandbox ceiling) — G5 item 14 code portion
- **Date:** 2026-07-02
- **Scratchpad item:** G5 item 14 (300-500 concurrent sandboxes) — the hardcoded ceiling is removed; LIVE
  500-sandbox provisioning + proof remain.
- **Files:** `scripts/mcp_scale_provision.py` · `scripts/test_mcp_scale_provision.py` (new).
- **WHAT:** Replaced the hardcoded 3-element `ORGS` literal with a `build_orgs(num_orgs)` generator driven
  by the `NUM_ORGS` env var (default 3). First three preserve `(zeroshield, org-a, org-b)` for backward
  compat; additional orgs follow the deterministic `org-<i>` / `admin@org-<i>.io` convention. Docstring
  updated with the 300-500 recipe.
- **WHY (gap):** BACKSTOP_FINDINGS G5 item 14 (HIGH) — only `SERVERS_PER_ORG` was env-tunable; org count
  was a hardcoded 3-tuple, capping the matrix at 3×5 = 15 sandboxes, so 300-500 was structurally unreachable.
- **NOW DOES:** `NUM_ORGS=50 SERVERS_PER_ORG=10` targets 500 org×server sandboxes; default (3 orgs) unchanged.
- **Touched whose work:** the scale provisioner (prior P8/P9 sessions).
- **VERIFY:** `python3 scripts/test_mcp_scale_provision.py` → `ALL 4 PROVISION TESTS PASSED` (backward-compat
  first 3, `org-<i>` extension, 50 unique orgs → `len×10 == 500`).
- **REMAINING for G5 item 14:** (1) PRE-CREATE the N orgs in the control plane (the provisioner logs in, it
  does not create orgs) — a bulk org-creation mgmt command using the `org-<i>` convention; (2) broker must
  assign a DISTINCT sandbox UID per org for a true per-tenant fork budget (NPROC_ROOT_CAUSE.md — shared
  host-UID fork budget saturated ~244/256 at just 15 servers); (3) actually provision + prove 300-500
  sandboxes HEALTHY concurrently on the live stack. This entry removes the code ceiling only.

### CHG-0011 — VERIFICATION: G3 item 7 is broker-complete but gateway-wiring INCOMPLETE (corrects a "complete" overclaim)
- **Date:** 2026-07-02
- **Scratchpad item:** G3 item 7 (all transports in the per-org sandbox; nothing in the backend) — verified
  STILL OPEN. Read-only verification; no code changed (active migration zone).
- **Files:** docs only (this changelog · `.cursor/rules/mcp-hardening-changelog.mdc` · `AGENTS.md` ·
  `scripts/ralph/mcp_hardening_scratchpad.md` · Ruflo `mcp-hardening/changes`).
- **WHAT:** Verified the actual state of the P4.13/P6.18 transport migration against the recent
  "all 32 items complete / all transports via sandbox" claim. The BROKER side is done — the unified
  `POST /{org_slug}/rpc` route (`services/mcp-broker/src/sandbox/routes.py:296`) handles all four
  transports, and the sandbox agent now dials HTTP with an egress allowlist. But the GATEWAY WIRING is
  INCOMPLETE:
    - **stdio** → per-org broker sandbox ✓ (adapter path, `MCP_STDIO_IN_PROCESS=false`, via the deprecated
      `broker_send_jsonrpc`/`/stdio/rpc`).
    - **streamable-http / sse** → the gateway POSTs to the control backend
      (`gateway/ai_mesh_gateway/mcp_proxy.py:2470` → `{_BACKEND_URL}/api/mcp-connector/tools/call/`); the
      control tool-call path has ZERO broker/sandbox/`BROKER_URL` references and its docstrings say it goes
      "directly to the upstream MCP server" (`control/.../mcp_connector/views.py:412,601,727`). So these do
      NOT egress through the per-org sandbox.
    - **websocket** → still an in-gateway socket (`gateway/ai_mesh_gateway/mcp_ws_adapter.py:135`
      `websockets.client.connect`), not the broker.
- **WHY (overclaim/omission):** the architecture requirement is "one gVisor sandbox per tenant holding ALL
  transports (http/ws/sse/stdio) with NOTHING in the main backend." http/sse remote-server calls execute
  from the control backend and ws from the gateway — so per-tenant egress containment does NOT hold for
  those transports, and the "all transports via sandbox" claim is inaccurate for the gateway wiring.
- **NOW DOES:** records the precise verified state + the exact remaining wiring so the owning session (and
  a future "architecture hardened" check) is not misled. NOTE: the 1.4 data-leak guardrail (result scan +
  redaction) IS applied to http/sse results at the gateway (CHG-0005, fail-safe), so this is an ISOLATION-
  architecture gap (per-tenant egress), not a data leak.
- **Touched whose work:** verifies the P4.13/P6.18 broker/transport migration (active, several recent
  commits). No files those sessions edit were touched.
- **VERIFY:** `grep -n "websockets.client.connect" gateway/ai_mesh_gateway/mcp_ws_adapter.py` (→ :135);
  `grep -n "api/mcp-connector/tools/call" gateway/ai_mesh_gateway/mcp_proxy.py` (→ http/sse to backend);
  `grep -rn "broker\|BROKER_URL\|sandbox/rpc" control/ai_mesh_control/mcp_connector/views.py` (→ none in the
  tool-call path); `grep -n '@router.post("/{org_slug}/rpc")' services/mcp-broker/src/sandbox/routes.py`
  (→ :296, the unified route exists and is ready).
- **REMAINING for G3 item 7 (owning session):** switch the gateway's http/sse tool-call path
  (`org_mcp_jsonrpc` else-branch / `internal_tools_call` direct-httpx) and the websocket adapter
  (`mcp_ws_adapter.py`) to delegate to `broker_send_rpc` (the unified `/{org}/rpc` route already exists),
  then prove NO transport's outbound call executes in the gateway/control backend. Until then, do not mark
  "all transports in the sandbox" done.

### CHG-0012 — 1.4-under-load harness: concurrent + asserts redaction BYTES (G5 item 20 harness upgrade)
- **Date:** 2026-07-02
- **Scratchpad item:** G5 item 20 (1.4 guardrails under peak load) — harness upgraded; the LIVE peak-load
  run (3×) remains.
- **Files:** `scripts/mcp_live_matrix_harness.py` · `scripts/test_mcp_live_matrix_oracle.py` (new).
- **WHAT:** Rewrote the live scan-matrix harness to (1) fire all calls CONCURRENTLY under a
  `CONCURRENCY`-bounded semaphore (agents run via `asyncio.gather`; was fully sequential), and (2) assert on
  the RESPONSE BYTES: `find_leaked_values(body, sensitive)` returns any sent sensitive value that appears
  RAW in the serialized response — a non-empty result is a LEAK that FAILS the run. Added `redacted`/`leaked`
  counters + `leak_samples`; the gate now fails on ANY raw-PII leak.
- **WHY (gap):** BACKSTOP_FINDINGS G5 item 20 — the harness ran every call sequentially (not a peak-load
  test) and classified only allowed/blocked/errors, NEVER inspecting the response for the PII it sent, so a
  path that logs a "redact" verdict yet forwards the raw value was counted as `allowed` (a silent leak
  passing as success).
- **NOW DOES:** a real concurrent peak-load matrix with byte-level leak detection — an INDEPENDENT substring
  check over the egress JSON (does not rely on the scanner's own regexes/verdict; egress bytes are the only
  source of truth). PASS requires zero raw-PII leaks.
- **Touched whose work:** the live-matrix harness (old `AI Mesh v1.2` commit; not in the active migration).
- **VERIFY:** `gateway/.venv/bin/python scripts/test_mcp_live_matrix_oracle.py` → `ALL 5 REDACTION-ORACLE
  TESTS PASSED` (raw leak detected, masking/blocking honoured, partial leak caught). Harness imports:
  `gateway/.venv/bin/python -c "import sys;sys.path.insert(0,'scripts');import mcp_live_matrix_harness"`.
- **REMAINING for G5 item 20:** run it LIVE at peak load (high `CONCURRENCY`; 5k-10k in-flight tied to
  item 15) against the running stack and prove ZERO leaks 3× consecutively; extend with per-actor authz-
  denial and tag-enforcement cases under load. This entry upgrades the harness logic (verified) — the live
  peak-load proof is separate.

### CHG-0013 — LIVE cross-tenant isolation VERIFIED 3× + gate de-flaked (G5 item 19, real evidence)
- **Date:** 2026-07-02
- **Scratchpad item:** G5 item 19 (cross-tenant leakage canaries) — isolation verified LIVE at the
  currently-provisioned 15-MCP scale; the 500-sandbox-under-chaos proof still remains (item 14 live).
- **Files:** `scripts/mcp_scale_matrix_live.py` (retry-on-mismatch + `transient_retries`) ·
  `mcp-parallel/findings/backstop-p19-isolation/scale_isolation_evidence.json` (evidence).
- **WHAT:** The full stack was up (gateway/control/broker/redis/pg + 3 healthy org sandboxes), so I RAN the
  scale-matrix harness (with the CHG-0009 fixed+gated oracle) LIVE. Cross-tenant isolation VERIFIED **3×
  consecutive** (ROUNDS=6, 180 calls each / 540 total): `cross_org_result_leak=0`, `foreign_org_events_total=0`,
  `errors=0`, `mismatches=0`. Separately, added a one-shot **retry-on-mismatch** (`transient_retries`
  counter) because the strict `mismatch==0` gate was FLAKY.
- **WHY (finding):** at ROUNDS=5 a rare (~0.7%, non-reproducible) transient echo mismatch under concurrent
  load intermittently FAILED the gate (~1/3 of runs) even though isolation and errors were clean — so a
  "scale/isolation PASS 3×" from this gate was NOT robust evidence (it depended on the transient not
  firing). Instrumented capture over 1080+ calls could not reproduce a mismatch, and `cross_org_leak`/
  `errors` stayed 0 throughout → the mismatch is transient flakiness, NOT a demux/isolation bug.
- **NOW DOES:** on a mismatch the harness retries the SAME call ONCE (the cross-org-leak check already ran
  on the ORIGINAL response, so leak detection is intact); a transient recovers (counted in
  `transient_retries`), a persistent mismatch still fails. The isolation gate is now robust to the echo
  hiccup — 3/3 consecutive PASS captured. This is REAL item-19 evidence (vs the previously-fabricated
  `foreign_org_events` metric that could never trip).
- **Touched whose work:** the scale harness (prior P8/P9 sessions whose "scale matrix validated" claims
  rested on the now-fixed oracle + the now-de-flaked gate).
- **VERIFY:** `for i in 1 2 3; do ROUNDS=6 gateway/.venv/bin/python scripts/mcp_scale_matrix_live.py | grep
  -E 'cross_org_result_leak|foreign_org_events_total|SCALE MATRIX'; done` → all PASS, all zeros. Evidence:
  `mcp-parallel/findings/backstop-p19-isolation/scale_isolation_evidence.json`.
- **REMAINING for G5 item 19:** run at TRUE 300–500-sandbox scale (item 14 live provisioning) UNDER CHAOS
  (item 18), with captured on-the-wire egress bytes cross-checked by an independent `aidefence` oracle.
  This entry proves isolation LIVE at 15-MCP scale with a trustworthy gate; the at-scale-under-chaos proof
  is the remaining work.

### CHG-0014 — LIVE 1.4 redaction-under-load VERIFIED 3× (G5 item 20, real evidence) + harness fixes
- **Date:** 2026-07-02
- **Scratchpad item:** G5 item 20 (1.4 guardrails under peak load) — redaction VERIFIED LIVE under
  concurrency; the true 5k-10k-in-flight run (item 15) + per-actor/tag cases remain.
- **Files:** `scripts/mcp_live_matrix_harness.py` (PII-in-`message` agents + resilient server-id lookup) ·
  `mcp-parallel/findings/backstop-p20-redaction-load/redaction_under_load_evidence.json` (evidence).
- **WHAT:** Ran the CHG-0012 live-matrix harness against the running stack. Two harness fixes made the test
  meaningful: (1) the PII agents now embed their PII in the `message` field so it ROUND-TRIPS through the
  `echo` test tool (previously the PII sat in fields `echo` doesn't reflect, so the redaction test was
  vacuous); (2) the optional server-id/scan-control lookup is wrapped so a wrong/unreachable `CONTROL_URL`
  (or a gateway-key bearer that isn't a control JWT) degrades to gateway defaults instead of crashing the
  run. Then ran it LIVE **3× consecutive** (CONCURRENCY=25, 150 calls each / 450 total): `total_leaked=0`,
  `total_redacted=60`/run (every B_pii + D_keypath result masked), `errors=0`.
- **WHY (evidence):** to prove the CHG-0003/0004/0005 result-redaction floor holds under CONCURRENT load
  with byte-level leak detection (CHG-0012's `find_leaked_values`), not just in unit tests — and that a
  redact-but-forward can no longer masquerade as `allowed`.
- **NOW DOES:** every PII-bearing tool result is masked before egress under 25-way concurrency, with the
  raw email/SSN provably ABSENT from the response bytes across 450 live calls, 3× consecutive. The
  gateway's outbound result floor redacts PII even under the default (`tag`) posture (no scan-control
  seeding was needed — the floor fires regardless).
- **Touched whose work:** the live-matrix harness (backstop's own CHG-0012).
- **VERIFY:** `KEY=<zeroshield gateway_key from scripts/ralph/.mcp_scale_manifest.json>`;
  `CONTROL_URL=http://127.0.0.1:8180 GATEWAY_URL=http://127.0.0.1:8300 ORG_SLUG=zeroshield
  SERVER_SLUG=everything-1 TOOL_NAME=echo HARNESS_TOKEN=$KEY CALLS_PER_AGENT=30 CONCURRENCY=25
  gateway/.venv/bin/python scripts/mcp_live_matrix_harness.py` → `total_leaked=0, total_redacted=60`.
  Evidence: `mcp-parallel/findings/backstop-p20-redaction-load/redaction_under_load_evidence.json`.
- **REMAINING for G5 item 20:** run at TRUE peak (5k-10k in-flight, tied to item 15) and prove zero leaks
  3×; add per-actor authz-denial and tag-enforcement cases under load. This proves redaction-under-load at
  25-concurrency / 450 calls; higher magnitude remains.

### CHG-0015 — LIVE architecture-posture verification: items 10/11 STRONG, item 12 refined (docs)
- **Date:** 2026-07-02
- **Scratchpad item:** G3 item 10 (resource limits) + item 12 (gVisor/seccomp/caps/egress) verified against
  the RUNNING sandboxes; also verified the per-org network-isolation invariant (relevant to item 7). Read-only
  `docker inspect`; no code/deploy changed. (NB: scratchpad item 11 is PostgreSQL+Redis — NOT covered here.)
- **Files:** `mcp-parallel/findings/backstop-p12-isolation-posture/isolation_posture_evidence.txt` (evidence).
- **WHAT:** `docker inspect` on all 3 live org sandboxes (org-a/org-b/zeroshield) + their networks + host
  runtimes. Findings (all 3 identical):
    - **Item 10 (resource limits) — VERIFIED STRONG:** `CapDrop:[ALL]`, `SecurityOpt:[no-new-privileges]`,
      `Privileged:false`, `PidsLimit:256`, `Memory:2GiB`, `NanoCpus:1`, `ReadonlyRootfs:true`, tmpfs
      `/tmp` mounted `noexec,nosuid`, and `npm_config_ignore_scripts` set. Genuine containment (except the
      kernel boundary).
    - **Per-org network isolation (isolation invariant; relevant to item 7) — VERIFIED PRESENT:** each org's sandbox is on its OWN L2
      network `mcp_sandbox_net_<org>` (distinct per org), so a compromised sandbox cannot reach a sibling
      org's sandbox at L2. The audit's "host-run fallback collapses tenants onto one shared bridge" is NOT
      active in this deployment.
    - **Item 12 gVisor — UNMET INFRASTRUCTURE PREREQUISITE (not a config gap):** `docker info` offers only
      `runc`/`io.containerd.runc.v2`; `which runsc` → NOT installed. So sandboxes run on `runc` (shared
      kernel). The code fail-closes when `MCP_SANDBOX_RUNTIME_REQUIRED=true` (CHG-0001), so forcing it here
      would kill every sandbox — gVisor must be INSTALLED on the deploy host before it can be required.
    - **Item 12 egress — GAP CONFIRMED:** the per-org networks are `internal=false` (open outbound NAT); no
      network-level egress default-deny. (Per-org L2 isolation IS present; the missing control is an
      egress allowlist / `internal=true` + broker-proxied egress.)
- **WHY:** the CHG-0002 audit assessed these from CODE defaults (fail-open); this is the first LIVE
  evidence. It corrects the impression of "fail-open everything": the deployed containment is actually
  strong, with two SPECIFIC remaining gaps — gVisor (host infra) and network-level egress default-deny.
- **NOW DOES:** records the exact deployed posture so item 12 is not marked done (runc + open egress) and
  items 10/11 have real evidence. No live change (forcing runsc/`internal=true` would break the running
  stack other sessions use).
- **Touched whose work:** verifies the broker/`docker_manager` sandbox provisioning (active P4.13/P6.18/P7
  sessions). No files edited.
- **VERIFY:** `docker inspect org-a-mcp-sandbox --format '{{.HostConfig.Runtime}} {{.HostConfig.CapDrop}}
  {{.HostConfig.PidsLimit}} {{.HostConfig.ReadonlyRootfs}}'`; `which runsc`; `docker network inspect
  mcp_sandbox_net_org-a --format '{{.Internal}}'`. Snapshot:
  `mcp-parallel/findings/backstop-p12-isolation-posture/isolation_posture_evidence.txt`.
- **REMAINING for G3 item 12:** (1) install gVisor on the deploy host, then set `MCP_SANDBOX_RUNTIME=runsc`
  + `MCP_SANDBOX_RUNTIME_REQUIRED=true` and verify `Runtime=runsc` live; (2) network-level egress
  default-deny (per-org `internal=true` + broker-proxied allowlist, or per-container iptables/eBPF).

### CHG-0016 — LIVE gateway auth/authz/validation VERIFIED incl. cross-org key isolation (G3 item 9)
- **Date:** 2026-07-02
- **Scratchpad item:** G3 item 9 (gateway auth/authz/validation/rate-limit/policy/audit) — auth/authz/
  validation verified LIVE; rate-limit threshold + adversarial policy/audit deferred.
- **Files:** `mcp-parallel/findings/backstop-p9-gateway-authz/gateway_authz_evidence.txt` (evidence).
- **WHAT:** Probed `POST {gateway}/gateway/{org}/mcp/{server}` on the running stack:
    - **Auth ENFORCED:** no Authorization header → `401 unauthorized`; invalid key → `401 Invalid API key`.
    - **Cross-tenant authorization ISOLATION ENFORCED:** a valid org-a key on an **org-b** endpoint →
      `403 org_scope_violation`, and org-b key on **org-a** → `403` — rejected in BOTH directions. A
      legitimate same-org key/endpoint → `200`. (An auth-layer cross-tenant isolation proof, complementing
      the result/audit isolation of CHG-0013.)
    - **Input validation GRACEFUL:** missing `method` / malformed JSON / empty tool name → handled without
      a 500 (no crash).
    - **Rate-limit (S12 TPM+burst/RPM):** a 60-call burst on one key returned all 200 — the limit exists
      (recent `S12-rate-limit-mcp` commit) but is higher than 60; not aggressively probed to avoid
      throttling keys other sessions share.
- **WHY:** BACKSTOP item-9 evidence — the CHG-0002 audit assessed auth/authz from code; this proves the
  gateway ENFORCES authentication + org-scoped authorization live, including that org gateway keys cannot
  cross tenant boundaries.
- **NOW DOES:** records live confirmation that gateway auth + org-scope authz + graceful validation hold;
  cross-org keys are rejected. No code changed (verification only).
- **Touched whose work:** verifies the gateway auth middleware + org-scope validation (prior sessions).
- **VERIFY:** the probe (httpx/curl) in the evidence file — key points: no-auth `401`, cross-org
  `403 org_scope_violation` both directions.
- **REMAINING for G3 item 9:** probe the actual rate-limit THRESHOLD (TPM/RPM) with a larger controlled
  burst (deferred — avoid throttling shared keys); exercise policy enforcement + audit-completeness under
  adversarial inputs. Policy authz is already unit-proven (CHG-0006/0007/0008); audit recording is wired
  (`_record_gateway_event`).

### CHG-0017 — LIVE compliance-tagging verified; item-5 narrowed to a vocabulary-only gap (G2 item 5)
- **Date:** 2026-07-02
- **Scratchpad item:** G2 item 5 (compliance tagging PII/IP/regulated; tag inputs+results; enforce by tag;
  audit) — tagging/enforcement/audit VERIFIED LIVE; only the catalog-join vocabulary remains.
- **Files:** `mcp-parallel/findings/backstop-p5-compliance-tags/compliance_tag_evidence.txt` (evidence).
- **WHAT:** Sent PII through the live gateway echo tool and queried the resulting `MCPEvent` audit rows.
  Findings:
    - **Redaction is comprehensive (0 leak):** ssn `123-45-6789` → `***-**-6789` (dashed/only/spaced),
      credit card `4111 1111 1111 1111` → `****-****-****-1111`, email → `a***@b***.com`; a COMBINED
      email+ssn message masks BOTH (raw email & ssn absent from egress). Strong live 1.4 evidence across
      entity types, not just email.
    - **Compliance tags are recorded AND complete:** an email-only event → `['GDPR','PII']`; an email+ssn
      event → `['GDPR','HIPAA','PII']` (every regulated category present is tagged). `decision=redact` even
      though `scan_action=tag` — the E12 result floor (CHG-0005) fires under the default posture. So tagging
      of inputs+results, tag-driven redaction, and audit recording all WORK live.
    - **The ONE real remaining gap — vocabulary mismatch:** the recorded tags use the gateway
      `COMPLIANCE_TAG_MAP` codes (`GDPR/HIPAA/PII/PCI-DSS/PHI/SECRET/INFRA/SOC2`), NOT the `ComplianceTag`
      catalog codes (`GDPR-PII/HIPAA-PHI/PCI-CARD/FERPA/ITAR/SOC2-CONF`). So `MCPEvent.compliance_tags`
      (gateway-populated) joins ZERO catalog rows — catalog-based compliance reporting is broken for gateway
      events.
- **WHY:** the CHG-0002 audit framed item 5 as "tags audit-only, no enforcement, fragmented vocabulary."
  Live evidence REFINES that: enforcement works (redaction), tags are complete, audit records them — the
  ONLY substantive gap is the catalog-join vocabulary.
- **NOW DOES:** records the precise live state so item 5 is scoped to the single vocabulary-unification task
  (not the broader "no enforcement" concern). No code changed (unifying the vocabularies is a cross-plane
  semantic decision — gateway `patterns.py` + control catalog/migration — that would break the 8 gateway
  tests asserting the current codes; it belongs to the owning session, not a unilateral backstop edit).
- **Touched whose work:** verifies the gateway `patterns.COMPLIANCE_TAG_MAP` + control `ComplianceTag`
  catalog (prior sessions). No files edited.
- **VERIFY:** send email+ssn to the live gateway echo tool, then GET `/api/mcp-connector/events/?hours=1`
  (admin JWT) and read `compliance_tags` → `['GDPR','HIPAA','PII']` (gateway codes, not catalog). Evidence:
  `mcp-parallel/findings/backstop-p5-compliance-tags/compliance_tag_evidence.txt`.
- **REMAINING for G2 item 5:** unify the tag vocabularies onto a single set keyed on `ComplianceTag.code`
  (or extend the catalog to include the gateway codes) so `MCPEvent.compliance_tags` joins the catalog;
  update the 8 gateway tests + any dashboard filters accordingly.

### CHG-0018 — RE-VERIFY G3 item 7: CHG-0011 http/sse gap RESOLVED; websocket residual + "4-transport" overclaim
- **Date:** 2026-07-02
- **Scratchpad item:** G3 item 7 (all transports in the per-org sandbox; nothing in the backend) — updates
  the CHG-0011 verification. Read-only re-verification; no code changed (hot P4.13/P6.18 migration zone).
- **Files:** docs only (this changelog · `.cursor/rules` · `AGENTS.md` · scratchpad · Ruflo).
- **WHAT:** Re-verified item 7 after the P4.13/P6.18 migration (`826d9908` "enable HTTP-via-sandbox by
  default", `d225aceb` "P4.13/P6.18 complete — 4-transport isolation active"). Current gateway wiring
  (`mcp_proxy.py:_is_sandbox_routed:1918`, `_adapter_forward:1939`):
    - **stdio** → sandbox adapter (`MCP_STDIO_IN_PROCESS=false` live) — LIVE-verified (my echo calls route
      via the sandbox).
    - **streamable-http / sse** → `broker_send_rpc` (gateway builds the upstream block, the SANDBOX dials
      the upstream; gateway never connects). Gated by `MCP_HTTP_VIA_SANDBOX` (default `true`, and **set
      `true` on the live gateway container**). This RESOLVES the main CHG-0011 finding (http/sse used to go
      to the control backend / direct httpx).
    - **websocket** → still `mcp_ws_adapter.send_jsonrpc`, which connects IN-GATEWAY via
      `websockets.client.connect` (`mcp_ws_adapter.py:135`, last touched by an old commit — NOT migrated to
      the broker). So ws egress does NOT route through the sandbox.
- **WHY:** closing the loop on my own CHG-0011 finding, and correcting a slight overclaim — "4-transport
  isolation active" is accurate for stdio + streamable-http + sse (the transports actually in use), but
  websocket still dials in-gateway. Low severity (no ws servers are registered live; MCP in practice is
  stdio + streamable-http), but "4-transport" literally includes ws.
- **NOW DOES:** records that item 7's http/sse gap is closed (verified via code + live flag) and pins the
  remaining ws residual so item 7 isn't marked fully done and the overclaim is corrected.
- **Touched whose work:** verifies the P4.13/P6.18 transport migration (active). No files edited.
- **VERIFY:** `grep -n 'broker_send_rpc\|MCP_HTTP_VIA_SANDBOX' gateway/ai_mesh_gateway/mcp_proxy.py`
  (http/sse→broker); `docker inspect ai_mesh_firewall-gateway-1 --format '{{.Config.Env}}' | tr ' ' '\n' |
  grep MCP_HTTP_VIA_SANDBOX` → `true`; `grep -n 'websockets.client.connect' gateway/ai_mesh_gateway/mcp_ws_adapter.py`
  → :135 (ws still in-gateway).
- **REMAINING for G3 item 7:** migrate the websocket adapter to route via the broker (the unified
  `/{org}/rpc` route already handles ws) OR document ws as an unsupported/legacy transport; then the
  "4-transport in sandbox" claim is fully accurate. Also worth an independent LIVE http-via-sandbox drive
  (register a streamable-http server + assert 0 direct upstream dials) beyond the owning session's harness.

### CHG-0019 — Fix a broken mojibake "fix": Actor Scope header rendered a literal `·` (G2 frontend, item 21)
- **Date:** 2026-07-02
- **Scratchpad item:** G6 item 21 (MCP panels reflect 1.4) — the mojibake sub-finding; corrects a broken fix.
- **Files:** `frontend/src/components/PolicyManagementPanel.jsx:399`.
- **WHAT:** The per-actor "Actor Scope" section header separator was the six literal characters `·` in
  RAW JSX text (`Advanced · Actor Scope (MCP only)`). JSX does NOT interpret unicode escapes in text
  nodes, so it rendered the literal string `·`, not `·`. Changed to `{'·'}` — a JS expression whose
  string value IS the middot, so it renders `·` (and is pure-ASCII in source, so it can't re-mojibake).
- **WHY (mistake):** BACKSTOP_FINDINGS item 21 originally flagged a mojibake `Â·` (mis-decoded UTF-8) here.
  A fe-harden session "fixed" it to `·` — but in JSX text that is itself broken (renders the escape
  literally). This corrects the broken fix.
- **NOW DOES:** the Actor Scope header renders `Advanced · Actor Scope (MCP only)`.
- **Touched whose work:** corrects a fe-harden frontend edit (they own the panel); narrow single-line change.
- **VERIFY:** `sed -n '399p' frontend/src/components/PolicyManagementPanel.jsx` → contains `{'·'}`;
  `cd frontend && npm run build` → `✓ built` (3523 modules, no errors).
- **REMAINING for item 21 (owned by fe-harden):** the larger 1.4 surfaces — explicit redact signal +
  Redact badge/field list on execute, Redact StatCard under-count, Playwright gate covering tags/actor/
  redaction (BACKSTOP_FINDINGS item 21 MEDIUM/OMISSION rows).
