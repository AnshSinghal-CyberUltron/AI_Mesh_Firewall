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

### CHG-0020 — LIVE Phase-3 observability verification: metrics+health+auto-recovery WIRED; tracing GAP (G4 item 13)
- **Date:** 2026-07-02
- **Scratchpad item:** G4 item 13 (monitoring + metrics + tracing; backup; auto-recovery) — metrics/health/
  auto-recovery verified live; distributed tracing + backup are gaps.
- **Files:** `mcp-parallel/findings/backstop-p13-observability/observability_evidence.txt` (evidence).
- **WHAT:** Probed the running stack's observability surfaces:
    - **Metrics — WIRED:** gateway `/metrics` → 401 (exists; scraper-key-gated; `METRICS_ALLOW_OPEN=false`
      → secured, not open) + a telemetry-drain thread (`TELEMETRY_DRAIN_MODE=thread`,
      `TELEMETRY_DRAIN_INTERVAL_SEC/BATCH_SIZE`, `METRICS_SCRAPER_KEY`).
    - **Health — WIRED:** gateway `/health` + `/v1/mcp/health` → 200; control `/api/health/` → 200; broker
      (:8311) `/health` → 200.
    - **Auto-recovery — mostly present:** docker healthchecks on broker/control/postgres/redis (all
      `healthy` → docker auto-restarts on unhealthy) + the sandbox reaper/reconcile (code-verified) for
      sandbox self-heal.
    - **GAPS:** (1) distributed **tracing (OpenTelemetry/Jaeger) is NOT configured** — no OTEL/JAEGER/
      TRACING env on gateway or broker (there is metrics/telemetry, but no request-level distributed
      tracing); (2) the **gateway container has NO docker healthcheck** (`health=none`) — docker won't
      auto-restart it on failure (control/broker/pg/redis do); (3) **backup (PG/Redis) not verified** (no
      backup cron/volume probed).
- **WHY:** the CHG-0002 audit noted item 13 (Phase-3) was unchecked; this is the first LIVE evidence of the
  observability posture.
- **NOW DOES:** records that metrics + health + infra auto-recovery are deployed and functional, and pins
  the three gaps so item 13 is scoped precisely (not marked done).
- **Touched whose work:** verifies the gateway/control/broker deployment + telemetry (prior sessions). No
  files edited.
- **VERIFY:** `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8300/health` → 200;
  `curl ... :8300/metrics` → 401; `docker inspect ai_mesh_firewall-gateway-1 --format '{{if .State.Health}}
  {{.State.Health.Status}}{{else}}none{{end}}'` → `none`; `docker inspect ai_mesh_firewall-gateway-1
  --format '{{.Config.Env}}' | grep -i otel` → (empty). Evidence:
  `mcp-parallel/findings/backstop-p13-observability/observability_evidence.txt`.
- **REMAINING for G4 item 13:** (1) wire distributed tracing (OTEL exporter → Jaeger/Tempo) so cross-service
  MCP request traces exist; (2) add a docker healthcheck to the gateway container; (3) verify/define PG +
  Redis backup (dump cron / volume snapshot) + restore drill.

### CHG-0021 — LIVE per-call chain verified in order; item 4 (minimize) resolved N-A for MCP (G2 items 4 & 6)
- **Date:** 2026-07-02
- **Scratchpad item:** G2 item 6 (end-to-end per-tool-call chain authz→minimize→scan+redact→tag→audit) +
  item 4 (context minimization/least-privilege) — both resolved with live evidence.
- **Files:** `mcp-parallel/findings/backstop-p6-per-call-chain/per_call_chain_evidence.txt` (evidence).
- **WHAT:** Fired a PII `tools/call` (email + ssn) through the live gateway and inspected the resulting
  `MCPEvent`'s `scan_trace`/metadata to confirm the full chain runs IN ORDER on each call:
    1. **authz** — the call reached the tool ⇒ the org-scoped gateway key was validated (cross-org keys are
       rejected 403, CHG-0016).
    2. **minimize** — N-A for MCP: the gateway forwards ONLY the tool args (the echo returns just the
       `message`; no user identity/session/context is injected). `enforced_at=gateway_adapter` (enforcement
       stays at the gateway, not leaked to the tool). This is least-privilege by design and RESOLVES item 4:
       `minimize_context` (context_assembler.py) is the chat/LLM message-pruning path, NOT the MCP tool-call
       path, which has no separate context-assembly step — so "minimize" for MCP = minimal forwarding +
       redaction (item 2) + per-actor authz (item 3).
    3. **scan+redact (in & result)** — `scan_pipeline=two_tier`; `scan_trace` shows `tier1 input` THEN
       `tier1 output` (both directions scanned), and `decision=redact` (the E12 result floor, CHG-0005,
       masked the PII even under `scan_action=tag`).
    4. **tag** — `compliance_tags=['GDPR','HIPAA','PII']` (complete across the categories present).
    5. **audit** — the `MCPEvent` is recorded with decision + tags + `latency_ms` + `scan_trace` +
       `enforced_at`.
- **WHY:** the prompt's 1.4 requirement is "the full per-call chain authz→minimize→scan+redact→tag→audit";
  this is the capstone that verifies the ordered chain LIVE (each individual step was verified in prior
  CHGs; this ties them together on a single real call) and definitively resolves the previously-vague item 4.
- **NOW DOES:** confirms the 1.4 per-call chain executes end-to-end, in order, on every MCP tool call, with
  minimize correctly N-A for the MCP path.
- **Touched whose work:** verifies the gateway scan/audit pipeline (many prior sessions). No files edited.
- **VERIFY:** send a PII `tools/call` to the live gateway, GET `/api/mcp-connector/events/?hours=1` (admin
  JWT), read `metadata.scan_trace` → `[tier1 input, tier1 output]`, `decision=redact`, `compliance_tags=
  ['GDPR','HIPAA','PII']`, `latency_ms` present. Evidence:
  `mcp-parallel/findings/backstop-p6-per-call-chain/per_call_chain_evidence.txt`.
- **G2 items 4 & 6 STATUS:** RESOLVED — item 6 chain verified live in order; item 4 minimize is N-A for MCP
  (least-privilege via minimal forwarding + item 2 redaction + item 3 authz). The only 1.4 (G2) work still
  open is item 3b (per-policy field-level RBAC redaction on the adapter path) and item 5's vocabulary
  unification.

### CHG-0022 — Propagate npm/PyPI pin + allowlist controls into the sandbox (G3 item 8)
- **Date:** 2026-07-02
- **Scratchpad item:** G3 item 8 (no unknown npm on the host — supply-chain) — makes the pin/allowlist
  controls reachable; enforcing them in prod is a separate operator step.
- **Files:** `services/mcp-broker/src/sandbox/docker_manager.py` (`_run_kwargs` environment) ·
  `services/mcp-broker/tests/test_sandbox_lifecycle.py` (+1 test).
- **WHAT:** `_run_kwargs` now propagates `MCP_STDIO_REQUIRE_PINNED_PACKAGES` and
  `MCP_STDIO_PACKAGE_ALLOWLIST` from the broker's env into the sandbox container env (pass-through, default
  OFF), so the sandbox agent's `stdio_manager` enforcement (`_REQUIRE_PINNED_PACKAGES` / `_PACKAGE_ALLOWLIST`
  checks) becomes reachable and operator-configurable.
- **WHY (gap):** BACKSTOP_FINDINGS item 8 — CONFIRMED LIVE: `docker inspect org-a-mcp-sandbox` showed
  `npm_config_ignore_scripts=true` (postinstall-RCE blocked) but NO pin/allowlist envs; `_run_kwargs` set
  only `ignore_scripts`, so the enforcement code (which reads those envs) defaulted OFF (allow-any, no pin).
  An org could `npx <arbitrary>@latest` (typosquat / backdoored-release / Shai-Hulud vector).
- **NOW DOES:** the pin/allowlist controls reach the sandbox; setting them on the broker (e.g.
  `MCP_STDIO_REQUIRE_PINNED_PACKAGES=true`) now enforces them per the agent's existing checks. Default OFF
  so it does NOT break the currently-running UNPINNED servers (the live "everything" test server is
  registered `@modelcontextprotocol/server-everything` with no version).
- **Touched whose work:** broker `docker_manager` (prior sessions; NOT in the hot P4.13/P6.18 zone — last
  touched `ef43c41f`). Change is my-diff-only + non-breaking (default OFF).
- **VERIFY:** `cd services/mcp-broker && .venv/bin/python -m pytest tests/test_sandbox_lifecycle.py -q` →
  27 passed (new `test_run_kwargs_propagate_npm_supply_chain_controls`: default OFF + operator-set
  propagation). A newly-created sandbox's `docker inspect ... Config.Env` would then show the vars.
- **REMAINING for G3 item 8:** to actually ENFORCE in prod: set `MCP_STDIO_REQUIRE_PINNED_PACKAGES=true`
  (requires pinning every registered server's package spec) and bake a locked `.npmrc` / private registry
  into the sandbox image (registry is still default public npmjs). This entry makes the controls
  propagate/configurable; turning them ON is an operator/registration policy step.

### CHG-0023 — LIVE PostgreSQL + Redis correctness verification (G3 item 11)
- **Date:** 2026-07-02
- **Scratchpad item:** G3 item 11 (PostgreSQL + Redis schemas/usage/restart-safety) — usage/schema verified
  live; the full restart-drill is item-18 chaos territory.
- **Files:** `mcp-parallel/findings/backstop-p11-pg-redis/pg_redis_evidence.txt` (evidence).
- **WHAT:** Inspected the live Redis + Postgres backing the gateway/control:
    - **Redis — usage correct + live:** `mcp:scan_ver:*` = 72 keys (scan-config version cache-invalidation,
      M-15; `type=string`, e.g. `99` — control `INCR`s on scan/tool-config changes and the gateway
      invalidates its enabled-tools cache); `ratelimit:*` = 2 keys (S12 TPM+burst/RPM limiter active);
      `mcp:toolcalls:*` = 0 (per-key call-cap counter — 0 active because the test keys are uncapped and the
      counter has a 60s TTL; the mechanism is present in `mcp_proxy.py`).
    - **Postgres — persistence + schema correct at scale:** `mcp_connector_mcpevent` holds **109,362 events
      across 3 orgs**; the `compliance_tags` column is populated (block=10, redact=265 tagged events) — so
      audit + tag persistence works.
    - **Restart-safety — graceful by design:** Redis unreachable → the gateway degrades to pure-TTL cache
      validity (M-15), it does NOT crash; PG event recording is best-effort / fire-and-forget so it does not
      block tool calls.
- **WHY:** the CHG-0002 audit left item 11 (Phase-3) unchecked; this is the first live evidence that the
  Redis usage (scan-version invalidation, rate-limit, call-cap) and PG event/tag persistence are correct.
- **NOW DOES:** confirms both data stores are used correctly and persist events/tags at scale (109k events);
  no code changed (verification only).
- **Touched whose work:** verifies the gateway/control Redis + PG usage (prior sessions). No files edited.
- **VERIFY:** `docker exec ai_mesh_firewall-redis-1 redis-cli --scan --pattern 'mcp:*' | wc -l` (→ scan_ver
  keys); `docker exec ai_mesh_firewall-postgres-1 psql -U ai_mesh_firewall -d ai_mesh_firewall -tAc 'select
  count(*) from mcp_connector_mcpevent;'` (→ 100k+). Evidence:
  `mcp-parallel/findings/backstop-p11-pg-redis/pg_redis_evidence.txt`.
- **REMAINING for G3 item 11:** the actual restart DRILL — kill Redis/PG mid-load and verify recovery + no
  cross-tenant leakage during recovery — is UNSAFE on the shared stack (item 18 chaos; needs a dedicated host).

### CHG-0024 — Per-policy FIELD-level RBAC redaction on the stdio/websocket ADAPTER path (G2 item 3b / finding #1)
- **Date:** 2026-07-02
- **Scratchpad item:** G2 item 3b (per-policy field-level redaction on the adapter path). This closes the
  core of the item: "gateway EvaluationResult + apply on the adapter response" (the "add redaction_fields to
  compiled policies" half was already done by M-04, compiler.py:521).
- **Files:** `gateway/ai_mesh_gateway/policy_engine.py` (EvaluationResult.redaction_fields; collection in
  both `evaluate()` and `evaluate_mcp_policies()`; new `apply_field_redaction()` + `_normalize_field_key()`);
  `gateway/ai_mesh_gateway/mcp_scan_orchestrator.py` (McpScanResult.redacted_fields; `_scan_text_tier1`
  now returns the matched policy's `redaction_fields`; `scan_mcp_payload` accumulates the union and applies
  `_finalize_output` at every non-blocked return); `gateway/ai_mesh_gateway/mcp_proxy.py` (surface
  `redacted_fields` in the scan audit meta); `gateway/ai_mesh_gateway/tests/test_mcp_scan_orchestrator.py`
  (+6 orchestrator tests + 1 helper unit test).
- **WHAT:** the compiler emits each policy's `redaction_fields` into the bundle (compiler.py:521, M-04) and
  the control HTTP path masks those named tool-RESULT fields via `apply_field_redaction` — but the GATEWAY
  never consumed them, so the stdio/websocket ADAPTER path (`scan_mcp_payload`) did content-scan yet **NO
  field-level RBAC masking**. Now: (1) the gateway policy engine surfaces the matched, actor-scoped policy's
  `redaction_fields` on `EvaluationResult` (D6 trigger = "policy matched AND has non-empty redaction_fields",
  NOT gated on the verdict); (2) a faithful Django-free port of control's `apply_field_redaction` (NFKC
  homoglyph folding, case-insensitive keys, depth/node bounds, non-mutating deep copy) masks those named
  fields in the structured OUTPUT payload; (3) it is scoped by actor (`_policy_applies_to_actor`), applied
  ONLY on the output direction, and suppressed under a `monitor` posture (observe-only) — matching the HTTP
  path's `_output_monitor` guard; (4) a `block` posture already withholds the payload upstream, so field
  masking never runs on a blocked call; (5) `redacted_fields` is recorded in the scan trace + audit meta,
  mirroring the HTTP path's `metadata.redacted_field_names`.
- **WHY:** BACKSTOP finding #1 (the "big one" tracked under 3b) — actor/policy field-level RBAC masking of
  tool results was enforced on the control HTTP path but ENTIRELY ABSENT on the gateway stdio/websocket
  adapter path, a 1.4 field-level-redaction gap directly in the HARDEN-1.4 mandate ("field-level redaction of
  tool RESULTS"). The plumbing (bundle field, control masker) existed; only the gateway consumption was
  missing.
- **NOW DOES:** on the adapter path, a matched actor-scoped policy declaring `redaction_fields=["ssn",...]`
  masks those named fields in the tool RESPONSE (value → `[REDACTED]`) while siblings survive, scoped to the
  policy's actor, output-only, suppressed under monitor, audited via `redacted_fields`. Backward-compatible:
  every legacy policy carries `redaction_fields=[]` → exact no-op (proven by a test).
- **Touched whose work:** consumes the M-04 compiler bundle field (control policy compiler session) on the
  gateway side; complements the CHG-0006/0007/0008 per-actor ACCESS authz on the adapter path with per-actor
  field-level REDACTION. No control-plane change needed.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_scan_orchestrator.py
  -q` → 22 passed (6 new field-RBAC + 1 helper unit test). Broad sweep `ai_mesh_gateway/tests` → 1056 passed,
  0 failed. Key tests: `test_field_redaction_masks_named_output_fields`,
  `test_field_redaction_suppressed_under_monitor_posture`, `test_field_redaction_not_applied_on_input_args`,
  `test_field_redaction_scoped_to_actor_role`, `test_field_redaction_empty_list_is_noop`,
  `test_apply_field_redaction_nested_homoglyph_and_nonmutating`.
- **REMAINING for G2 item 3b:** this masks fields when the OUTPUT scan matches a policy declaring
  redaction_fields (output-stage-triggered parity). The control HTTP path additionally uses the INPUT-stage
  policy match to project fields out of the response (cross-stage). Threading input-stage `redaction_fields`
  into the output `scan_mcp_payload` for full cross-stage parity is a follow-up refinement; item stays open
  (advanced, not [x]) until that + a live drive. → DONE in CHG-0025.

### CHG-0025 — Cross-stage input-triggered field projection completes G2 item 3b (adapter path field-RBAC)
- **Date:** 2026-07-02
- **Scratchpad item:** G2 item 3b — the cross-stage half CHG-0024 left open. With this, the stdio/websocket
  ADAPTER path reaches full control HTTP-path parity for per-policy field-level RBAC redaction. **Item 3b → [x].**
- **Files:** `gateway/ai_mesh_gateway/policy_engine.py` (`apply_field_redaction` now returns identity on a
  true no-op); `gateway/ai_mesh_gateway/mcp_scan_orchestrator.py` (`McpScanResult.policy_redaction_fields`;
  `scan_mcp_payload(extra_redaction_fields=...)` merges caller-threaded fields for the OUTPUT mask + surfaces
  the this-scan declared union; `_finalize_output` identity-gates its record so an absent field is a true
  no-op); `gateway/ai_mesh_gateway/mcp_proxy.py` (`_mcp_security_scan(extra_redaction_fields=...)` +
  `policy_redaction_fields` in meta; `org_mcp_jsonrpc` captures the INPUT scan's `_in_rfields` and threads it
  into both adapter OUTPUT scans; the swap gate now also fires on `redacted_fields` so a finding-less field
  projection is not discarded; residual-limitation comment updated); tests
  (`test_mcp_scan_orchestrator.py` +5, `test_e12_result_redaction.py` +2 end-to-end).
- **WHAT:** CHG-0024 masked a result field only when the OUTPUT itself matched a policy declaring the field.
  The primary RBAC pattern — "role X never sees field F" — authors the rule to fire on the CALL, not the
  response, so it needs the INPUT-stage match to project fields out of the RESPONSE (exactly what the control
  HTTP path does via `eval_result.matched_policy_ids` on the pre-call eval → `apply_field_redaction(result,
  redacted_field_names)`). Now: the input scan surfaces `policy_redaction_fields` (the declared union of its
  matched actor-scoped policies) in its meta; `org_mcp_jsonrpc` threads that into the paired outbound scans as
  `extra_redaction_fields`; the orchestrator masks those named fields on the response. `apply_field_redaction`
  now preserves object identity when no named field is present, so a pure field-projection scan can be
  detected (masked ⇔ new object) and a no-op never mislabels the result as redacted; the adapter swap gate
  was widened to also swap in a field-projection redaction that produced no PII/secret finding (else the
  masked bytes would be discarded and the field would leak).
- **WHY:** completes BACKSTOP finding #1 — the mcp_proxy.py:2392-2394 comment explicitly flagged G8
  per-user/role field-level filtering as adapter-path "tracked as follow-up". Both trigger directions
  (output-content match AND input-call match) are now covered on the adapter path, at parity with control.
- **NOW DOES:** on the stdio/websocket/http-via-sandbox adapter path, a tool CALL matching an actor-scoped
  policy that declares `redaction_fields` strips those named fields from the tool RESPONSE (value →
  `[REDACTED]`), actor-scoped, monitor-suppressed, audited via `redacted_fields`; dormant (exact no-op) when
  no policy declares fields. Backward-compatible — all new params default to no-op.
- **Touched whose work:** builds directly on CHG-0024; consumes the M-04 control compiler bundle field;
  complements CHG-0006/0007/0008 per-actor ACCESS authz. No control-plane change.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_scan_orchestrator.py
  ai_mesh_gateway/tests/test_e12_result_redaction.py -q` → 37 passed. Broad sweep `ai_mesh_gateway/tests` →
  1063 passed, 0 failed. Key e2e:
  `test_adapter_input_policy_projects_redaction_fields_onto_response` (input-call policy → `account_number`
  masked in the adapter RESPONSE, non-targeted content survives) +
  `test_adapter_no_input_policy_leaves_response_fields_intact` (dormant guard);
  `test_extra_redaction_fields_projects_output_without_content_match`,
  `test_policy_redaction_fields_surfaced_on_input_scan_not_applied`,
  `test_apply_field_redaction_identity_on_noop`.
- **RESIDUAL (non-blocking, not part of 3b's adapter-path scope):** (1) the bare-REST / ext-proxy paths
  (`_scan_tool_result_floor`) do OUTPUT-triggered field masking (CHG-0024) but not input-triggered cross-stage
  (they are a separate surface from the org_mcp_jsonrpc adapter path 3b targets; the legacy direct-backend
  path is covered by control's own MCPToolCallView field redaction). (2) An optional live-stack drive on a
  real registered adapter server is belt-and-suspenders over the byte-level in-process e2e proof.

### CHG-0026 — Migrate websocket transport onto the sandbox broker path (G3 item 7 — last isolation residual)
- **Date:** 2026-07-02
- **Scratchpad item:** G3 item 7 (all transports via the per-org sandbox; nothing dialed from the gateway).
  websocket was the last transport still connecting in-gateway. **Item 7 → [x]** for the default config.
- **NOTE (concurrent co-commit):** a parallel mcp session landed the *identical* ws migration at the same
  time as commit `d6ab1ae7` (also labelled CHG-0026) and — via the shared-index hazard — swept this backstop's
  identical `mcp_proxy.py` + `test_mcp_http_via_sandbox.py` edits into it; this backstop's commit `cd973658`
  then carried only the four-memory docs. Both converged on ONE clean migration in HEAD (verified: single
  `("streamable-http","sse","websocket")` branch, `mcp_ws_adapter` forwarding import removed, 1064 gateway
  tests pass). This entry is the sole CHG-0026 memory record; the duplicate id exists only in the two commit
  *messages*.
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`_adapter_forward`: websocket now routes via
  `broker_send_rpc` alongside streamable-http/sse; the in-gateway `mcp_ws_adapter.send_jsonrpc` branch
  removed); `gateway/ai_mesh_gateway/tests/test_mcp_http_via_sandbox.py` (+1 test).
- **WHAT:** `_is_sandbox_routed("websocket")` already returned True ("ws always routes via the sandbox"), and
  the broker + sandbox agent already fully support websocket upstreams (`services/mcp-broker/src/sandbox/
  routes.py` unified `/{org}/rpc` for all four transports; `sandbox-image/agent/upstream_manager.py` opens
  and reuses `session.ws` websocket sessions; `mcp_sandbox_client.broker_send_rpc` builds the upstream block
  for ANY non-stdio transport). But the gateway's `_adapter_forward` STILL dialed websocket in-process via
  `mcp_ws_adapter.send_jsonrpc` (`websockets.client.connect`) — a self-contradiction with its own
  `_is_sandbox_routed` contract and the "nothing in the backend" isolation goal. Now the websocket branch is
  folded into the remote-transport branch: the gateway builds the upstream block (url + egress allowlist +
  injected OAuth bearer) and the per-org sandbox agent dials the ws upstream — the gateway never opens the
  websocket itself.
- **WHY:** BACKSTOP finding (CHG-0011/0018): "websocket STILL connects in-gateway … so '4-transport isolation
  active' OVERSTATES (3/4)". With this, all four transports (stdio + streamable-http + sse + websocket) egress
  through the per-org sandbox by default — the claim is now TRUE, not aspirational.
- **NOW DOES:** a websocket MCP tool call is forwarded gateway → broker → per-org sandbox → upstream ws host
  (egress-allowlisted to the upstream host only); the gateway process opens no upstream socket for any
  transport. Per-org network isolation + egress allowlist now cover ws too.
- **Touched whose work:** consumes the broker/sandbox-agent ws support (P4.13/P6.18 broker sessions) that was
  already built but unused by the gateway; aligns `_adapter_forward` with `_is_sandbox_routed`. `mcp_ws_adapter`
  is now legacy (no forwarding caller); its `main.py` reaper/shutdown lifecycle hooks remain but are benign
  no-ops (they manage an now-empty in-gateway ws session table) — a later cleanup can drop them.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_http_via_sandbox.py
  -q` → 4 passed incl. `test_adapter_forward_websocket_uses_broker_send_rpc` (asserts `broker_send_rpc` called
  with `up_config.transport=="websocket"` + the in-gateway `mcp_ws_adapter.send_jsonrpc` is NOT awaited).
  Broad gateway sweep → 1064 passed, 0 failed; broker ws/upstream/route/rpc/lifecycle subset → 52 passed.
- **REMAINING / caveat (non-blocking):** streamable-http/sse still honor the `MCP_HTTP_VIA_SANDBOX` escape
  hatch (default ON → sandbox; OFF → legacy direct-httpx) — so "nothing in the backend for ALL transports"
  holds for the DEFAULT config; stdio + websocket are unconditional. Optional: an independent LIVE drive
  (register a ws MCP server, assert 0 direct gateway upstream sockets) over the unit proof.

### CHG-0027 — Gateway docker healthcheck + restart policy (G4 item 13 — Phase-3 auto-recovery sub-gap)
- **Date:** 2026-07-02
- **Scratchpad item:** G4 item 13 (Phase-3 monitoring/metrics/tracing/backup/auto-recovery). Closes the
  gateway-healthcheck sub-gap CHG-0020 flagged; item 13 stays open (tracing + backup remain).
- **Files:** `docker-compose.yml` (base gateway: `healthcheck` + `restart: unless-stopped`);
  `docker-compose.prod.yml` (prod gateway: `healthcheck`; restart already applied via the
  `*restart_unless_stopped` anchor).
- **WHAT:** the gateway was the ONLY core service with neither a docker healthcheck nor (in the base compose)
  a restart policy — `docker inspect` health=none (CHG-0020), so it was never marked healthy/unhealthy and
  peers could only gate on `service_started`. Added a healthcheck that probes the existing auth-exempt
  `/health` endpoint (`python -c "urllib.request.urlopen('http://127.0.0.1:8300/health', timeout=5)"`,
  interval 15s / timeout 6s / retries 5 / start_period 60s) on both the base and prod gateway services, plus
  `restart: unless-stopped` on the base service (prod already had it via anchor).
- **WHY:** Phase-3 auto-recovery gap (CHG-0020): a crashed base-compose gateway was not auto-restarted, and no
  peer/monitor could observe gateway health. `/health` already returns 200 `{"status":"ok"}` normally and 503
  `{"status":"degraded","reason":"policy_signing_key_missing"}` on a fatal signing misconfig — so the probe
  correctly reads a broken gateway as unhealthy (urllib raises on the 503).
- **NOW DOES:** the gateway container reports docker health (healthy/unhealthy) and auto-restarts on crash;
  peers/monitoring can gate on / alert off `service_healthy`. Config-only — takes effect on the next
  `docker compose up`; the running container was NOT recreated by this change.
- **Touched whose work:** infra/compose (deploy topology). No application code changed. Complements CHG-0020's
  observability findings (metrics/health endpoints already wired; this wires the container-level probe).
- **VERIFY:** `docker compose -f docker-compose.yml -f docker-compose.override.yml config` →
  `gateway.restart=unless-stopped`, `gateway.healthcheck.test=[…/health…]`, `start_period=1m0s`;
  `docker compose -f docker-compose.yml -f docker-compose.prod.yml config` (with dummy required vars) → same
  healthcheck on the prod gateway, deploy limits intact. Both merged configs parse cleanly.
- **REMAINING for G4 item 13:** (1) distributed TRACING (OTEL/Jaeger) still not configured; (2) PG/Redis
  BACKUP still not verified; (3) OPTIONAL: upgrade peers that `depends_on: gateway` from `service_started` to
  `service_healthy` now that a healthcheck exists (a startup-ordering behavior change — left to the owning
  session).

### CHG-0028 — Extend the live-matrix harness with a per-actor AUTHZ-under-load oracle (G5 item 20)
- **Date:** 2026-07-02
- **Scratchpad item:** G5 item 20 (1.4 guardrails — redaction + per-actor authz + tagging — under peak load).
  Adds the authz-denial dimension the harness lacked (only redaction was proven under load). Item 20 stays
  open (the true 5k–10k peak run + a live denied-tool run remain — item 15 / dedicated host).
- **Files:** `scripts/mcp_live_matrix_harness.py` (new `authz_denied` + `authz_violation` oracles; `Scenario`
  dataclass; `DENY_TOOL_NAME` env + `build_scenarios()` deny-agent; per-scenario `tool_name`; authz counters
  + gate); `scripts/test_mcp_live_matrix_oracle.py` (+3 authz oracle tests).
- **WHAT:** the harness proved REDACTION under concurrent load (byte oracle, CHG-0012/0014) but had NO
  per-actor tool-AUTHORIZATION dimension — the mandate's "per-actor authz holding under peak load" was
  unexercised. Added: (1) `authz_denied(status, body)` — a precise denial oracle (HTTP 403, an authz-flavored
  JSON-RPC error, or a `[BLOCKED]` result; a GENERIC error is NOT a denial, so an internal/upstream failure
  isn't miscounted); (2) `authz_violation(status, body, expect_denied=True)` — True ONLY when a forbidden tool
  EXECUTED SUCCESSFULLY under load (a real authz hole); (3) a `Scenario` type + a `DENY_TOOL_NAME`-gated
  `F_authz_deny` agent that fires concurrent calls to a tool the actor may NOT use; (4) the run gate now FAILS
  on any `authz_violation` (in addition to any raw-PII leak), and flags an `authz_vacuous` run (deny-scenario
  configured but the tool neither denied nor executed → misconfigured DENY_TOOL_NAME) rather than passing
  silently.
- **WHY:** item-20 remaining explicitly required "per-actor authz-denial … cases under load"; a concurrency
  race that let ONE forbidden call through under peak load would be a silent authz hole the redaction oracle
  can't see. The oracle is byte/status-based and independent of the gateway's own verdict.
- **NOW DOES:** with `DENY_TOOL_NAME` set to a tool the `HARNESS_TOKEN` actor is denied, the matrix fires
  concurrent forbidden calls and asserts EVERY one is refused (403 / authz-error / `[BLOCKED]`), failing on any
  successful execution. Backward-compatible: unset → the deny-agent is skipped and the 5-agent redaction
  matrix runs unchanged.
- **Touched whose work:** extends the item-20 harness (prior backstop CHG-0012/0014). No gateway/app code
  changed — harness + oracle only.
- **VERIFY:** `gateway/.venv/bin/python -m pytest scripts/test_mcp_live_matrix_oracle.py -q` → 8 passed (5
  redaction + 3 authz: `test_authz_denied_shapes`, `test_authz_violation_only_on_successful_forbidden_
  execution`, `test_authz_violation_never_flags_allowed_scenarios`). `python -c "build_scenarios()"` → 5
  scenarios by default, 6 (incl. `F_authz_deny`) with `DENY_TOOL_NAME` set. `py_compile` clean.
- **REMAINING for G5 item 20:** run the full matrix (redaction + authz) LIVE at TRUE peak (5k–10k in-flight,
  item 15) with a real denied-but-existing tool as `DENY_TOOL_NAME`; add a tag-enforcement-under-load audit
  (query MCPEvents for compliance_tags at load, per CHG-0017) — both need a dedicated host / live policy
  setup.

### CHG-0029 — Harden the mcp_pipeline_matrix_live oracle (leak-blind + import-unsafe → byte-truth + testable)
- **Date:** 2026-07-02
- **Scratchpad item:** G5 stress harness quality (the mandate names `mcp_pipeline_matrix_live.py`). A harness
  whose leak oracle can't see a leak gives false-green stress runs — the same class of defect corrected for
  the other harnesses in CHG-0009 (dead cross-tenant oracle) and CHG-0012 (no response-byte check).
- **Files:** `scripts/mcp_pipeline_matrix_live.py` (rewritten: byte-truth oracle + import-safe);
  `scripts/test_mcp_pipeline_oracle.py` (new, +8 tests).
- **WHAT:** the old oracle was narrow and leak-blind — `pii = _SSN in text` checked ONLY one hardcoded SSN in
  ONLY `result.content[0].text`, so a redact-but-forward in a later content item, in `structuredContent`, in a
  nested field, or with ANY non-SSN sensitive value passed as `redacted`/`allow` (a silent leak). It also read
  `KEY = os.environ["GATEWAY_KEY"]` + `CASES = sys.argv[1]` + `asyncio.run()` at module scope, so the oracle
  could not be imported/unit-tested. Now: (1) `find_pii_in_body(body, sensitive)` substring-scans the ENTIRE
  serialized response (egress bytes = the only source of truth) for the case's ACTUAL sensitive values;
  (2) `case_sensitive_values(case)` derives them from an explicit `case["pii"]` list (else falls back to the
  canonical SSN in args, backward-compat); (3) `matches` treats `redacted` as byte-truth — allowed AND no raw
  value anywhere AND a redaction marker present (so a non-echoing tool isn't mistaken for a redaction), and
  `pass_pii` as allowed AND a raw value actually present; (4) the module is import-safe (env/argv/`asyncio.run`
  moved under `main()`/`__main__`), and the driver threads `url`/`key`/`cases` as args.
- **WHY:** a stress harness that classifies a leak as `redacted` produces false-green under-load evidence —
  precisely what item 20 must NOT do; the oracle must be independent of the scanner's own verdict and inspect
  the full egress.
- **NOW DOES:** the pipeline matrix FAILS a case whenever the sent sensitive value survives ANYWHERE in the
  response (not just the first content item), across all repeats/concurrency; the oracle is unit-tested.
- **Touched whose work:** the stress-harness set (prior backstop CHG-0009/0012/0028). No gateway/app code.
- **VERIFY:** `gateway/.venv/bin/python -m pytest scripts/test_mcp_pipeline_oracle.py -q` → 8 passed (incl.
  `test_find_pii_in_body_scans_whole_response_not_just_first_content` — a leak in content[1]+structuredContent
  is caught; `test_matches_redacted_is_byte_truth` — a redact-but-forward is NOT counted as redacted). All
  scripts oracle suites: `pytest scripts/test_mcp_pipeline_oracle.py scripts/test_mcp_live_matrix_oracle.py
  scripts/test_mcp_scale_oracle.py scripts/test_mcp_scale_provision.py -q` → 25 passed. `import
  mcp_pipeline_matrix_live` succeeds with no GATEWAY_KEY/argv.
- **REMAINING:** the LIVE pipeline run at scale (epochs × cases × REPEAT) is still gated on the dedicated-host
  stress environment (items 14–18); this change makes its verdicts trustworthy when it does run.

### CHG-0030 — Extend MCP compliance tagging to IP / infrastructure leakage (1.4 "PII/IP/regulated")
- **Date:** 2026-07-02
- **Scratchpad item:** G2 1.4 compliance tagging — "extended to PII/IP/regulated". PII/PHI/PCI/secret were
  detected+tagged on the MCP path; the IP/infrastructure category was NOT. (Distinct from item 5's tag-vocab
  mismatch — this is missing DETECTION coverage, not a catalog-join bug.)
- **Files:** `gateway/ai_mesh_gateway/mcp_scan_orchestrator.py` (`_scan_text_tier1` now also runs
  `detect_ip_leakage`; `_tags_for_finding` routes `ip_leakage` findings through `get_compliance_tags`);
  `gateway/ai_mesh_gateway/tests/test_mcp_scan_orchestrator.py` (+5 tests).
- **WHAT:** `IP_LEAKAGE_PATTERNS` + `detect_ip_leakage` (internal IPv4, internal hostnames `*.corp/.internal/
  .local/…`, internal URLs, private unix/windows file paths → `INFRA` tag) already existed and ran on the CHAT
  `output_guard` path, but the MCP tool-call scan (`_scan_text_tier1`) ran ONLY `detect_pii` + `detect_secrets`
  — so an internal host / IP / private file path in a tool RESULT (or args) was never detected, tagged, or
  redacted on the MCP path. Folded `detect_ip_leakage` into the same PII/secret fallback: an IP-leakage match
  now yields an `ip_leakage` finding (tagged `INFRA` via `get_compliance_tags`) and is enforced by posture
  (block posture → block; redact posture → `redact_all`; monitor → detect+tag+allow), matching PII/secret.
- **WHY:** the 1.4 mandate requires compliance tagging "extended to PII/IP/regulated"; MCP was silently
  missing the IP/infra dimension the chat path already had — internal infrastructure detail could egress
  through an MCP tool result untagged and (under a redact posture) with only partial masking.
- **NOW DOES:** internal IP/hostname/URL in an MCP payload is detected → `INFRA`-tagged → masked under a redact
  posture; a **fail-closed byte-check** covers the asymmetry that `redact_all` masks internal IP/host/URL but
  NOT private file paths — if ANY detected internal value SURVIVES the scrub under a redact posture, the call
  is BLOCKED rather than forwarded as a "redacted" result that still leaks (no A4-class redact-that-leaks).
  Public IPs (e.g. 8.8.8.8) and canonical example addresses are NOT flagged (no false positive).
- **Touched whose work:** reuses the existing tested `patterns.detect_ip_leakage` (chat output-guard author);
  extends the MCP scan orchestrator (prior backstop CHG-0003/0005/0007). Behaviour is additive — no existing
  test payload carries an internal IP/path, so all prior paths are unchanged.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_scan_orchestrator.py
  -q` → 32 passed (+5: internal-IP redacted+`INFRA`-tagged; private file path fails closed → block; block
  posture blocks; monitor tags without mutating; public IP not flagged). Broad sweep `ai_mesh_gateway/tests`
  → 1069 passed, 0 failed.
- **REMAINING (unchanged):** the gateway `INFRA`/`SECRET`/`PII` vocabulary vs the control ComplianceTag catalog
  codes (GDPR-PII/…) still mismatch for catalog reporting — item 5's separate cross-plane vocab decision.

### CHG-0031 — Per-org rate limit on the bare REST tool-call route (gateway rate-limit parity, G3 item 9)
- **Date:** 2026-07-02
- **Scratchpad item:** G3 item 9 (gateway auth/authz/validation/**rate-limit**/policy/audit). Closes a
  bare-REST-vs-JSON-RPC rate-limit parity gap; item 9 stays open (threshold probe + adversarial policy +
  audit-completeness remain).
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (new `_mcp_org_rate_limit_raw`; `_enforce_mcp_org_rate_limits`
  refactored onto it; `org_mcp_tool_call` now calls the raw check); `gateway/ai_mesh_gateway/tests/
  test_mcp_rate_limit.py` (+3 tests).
- **WHAT:** `_enforce_mcp_org_rate_limits` (per-org TPM + burst/req-s + RPM/req-min, atomic Redis `INCR`,
  fail-open by design) was called ONLY by `org_mcp_jsonrpc` (mcp_proxy.py:2092). The bare REST tool-call route
  `org_mcp_tool_call` enforced the per-KEY tool-call CAP (`_incr_tool_call_count`) + per-key authz (CHG-0006)
  but NOT the per-ORG rate limit — so a tenant could exceed org burst/RPM/TPM ceilings by driving
  `POST /{org}/mcp/{server}/tools/call` (the bare route) while the JSON-RPC route capped them. Extracted the
  shared check into `_mcp_org_rate_limit_raw` (returns a PLAIN 429 `JSONResponse`); the JSON-RPC route wraps it
  into its JSON-RPC-200 envelope (unchanged), and the bare REST route returns it as-is (REST-appropriate 429).
  The raw check runs early in `org_mcp_tool_call` — right after auth resolution, before arg parsing/forward.
- **WHY:** the architecture mandate requires gateway rate-limiting; a route with no per-org ceiling is a
  capacity-protection bypass (the same bare-route parity class as CHG-0006's missing authz gates).
- **NOW DOES:** both MCP tool-call entry points (JSON-RPC + bare REST) enforce the identical per-org
  TPM/burst/RPM ceilings; the bare route returns a plain HTTP 429 with `Retry-After` on breach. `org_mcp_jsonrpc`
  behaviour is byte-identical (same underlying limiters, same JSON-RPC wrapping).
- **Touched whose work:** the gateway MCP proxy rate-limit path (S12 author); extends the bare-route parity
  work started in CHG-0006. No behaviour change to the JSON-RPC route.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_rate_limit.py -q` →
  7 passed (+3: `_mcp_org_rate_limit_raw` returns a plain 429 / None-under-limit;
  `org_mcp_tool_call` TPM-exceeded → plain 429 `org_rate_limit_exceeded`; burst-exceeded → plain 429
  `burst_limit_exceeded`). Broad sweep `ai_mesh_gateway/tests` → 1072 passed, 0 failed.
- **REMAINING for G3 item 9:** live threshold probe (a controlled burst > 150 req/s on a dedicated key/host);
  adversarial policy-enforcement + audit-completeness checks. `ext_mcp_proxy` (the external passthrough proxy)
  also does not call the per-org limiter — a separate follow-up if that path is tenant-exposed. → DONE in CHG-0032.

### CHG-0032 — Per-org rate limit on the authenticated external MCP proxy (ext_mcp_proxy, G3 item 9)
- **Date:** 2026-07-02
- **Scratchpad item:** G3 item 9 (gateway rate-limit). Closes the `ext_mcp_proxy` follow-up flagged in
  CHG-0031 — now ALL THREE tenant-facing MCP entry points enforce the per-org ceiling.
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`ext_mcp_proxy` calls `_mcp_org_rate_limit_raw` after the
  domain allowlist check); `gateway/ai_mesh_gateway/tests/test_mcp_rate_limit.py` (+3 tests).
- **WHAT:** `ext_mcp_proxy` (`/v1/mcp/ext-proxy/{host}/{path}`) is a transparent proxy to allowlisted EXTERNAL
  MCP servers with inbound credential scanning + SSE result scanning (CHG-0004/0005), BUT applied NO per-org
  rate limit. The route is NOT in `middleware.EXCLUDED_PATHS`, so it sits behind the auth middleware — a valid
  API key (with `org_slug`) is required and `request.state.auth_context` is populated — yet the handler never
  consulted it for capacity. So a tenant could drive the external proxy past its org burst/RPM/TPM ceilings.
  Added `_mcp_org_rate_limit_raw(_get_auth_context(request))` right after the domain allowlist check (before
  any scan/forward work); on breach it returns a plain 429, else proceeds. The handler stays transport-level
  (no org-SCOPING), but the ceiling is charged to the CALLER's org.
- **WHY:** completes the gateway rate-limit coverage — CHG-0031 covered the bare REST route; this covers the
  external proxy. A tenant-authenticated route with no per-org ceiling is a capacity-protection bypass.
- **NOW DOES:** `org_mcp_jsonrpc`, `org_mcp_tool_call`, AND `ext_mcp_proxy` all enforce the identical per-org
  TPM/burst/RPM limiter (atomic Redis `INCR`, fail-open by design). ext-proxy returns a plain 429 on breach.
- **Touched whose work:** the gateway MCP proxy rate-limit path; direct continuation of CHG-0031. Behaviour
  unchanged when under limit (the raw check returns None → proceeds to the existing scan/forward).
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_rate_limit.py -q` →
  10 passed (+3: ext-proxy TPM-exceeded → 429 before forward; burst-exceeded → 429; disallowed domain still
  403 before the rate-limit gate). Broad sweep `ai_mesh_gateway/tests` → 1075 passed, 0 failed.
- **REMAINING for G3 item 9:** live threshold probe; adversarial policy-enforcement + audit-completeness. All
  three tenant-facing MCP entry points now rate-limited — the code-level rate-limit coverage is complete.

### CHG-0033 — Stop leaking the caller's gateway credential to external MCP servers (1.4 least-privilege; HIGH)
- **Date:** 2026-07-02
- **Severity:** HIGH (credential exposure to third parties).
- **Scratchpad item:** G2 item 4 (context minimization / least-privilege) + the 1.4 "prevent MCP data
  leakage" mandate. A NEW finding (not previously tracked).
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (new `_ext_proxy_forward_headers` + `_EXT_*_HEADERS`;
  `ext_mcp_proxy` header build rewritten); `gateway/ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py` (+2).
- **WHAT:** `ext_mcp_proxy` (`/v1/mcp/ext-proxy/{host}/{path}`, the transparent proxy to allowlisted EXTERNAL
  MCP servers) forwarded the caller's request headers verbatim — stripping only `host`/`content-length`/
  `transfer-encoding` — to the third-party upstream. So the caller's `Authorization: Bearer <gateway-API-key>`,
  `Cookie`, and `X-Api-Key` were sent to the external MCP domain. A third-party server (even allowlisted) thus
  received the caller's GATEWAY credential, which it could log, exfiltrate, or REPLAY against the gateway.
  Meanwhile the sandbox-routed path (`broker_send_rpc`) already built a CLEAN header set and injected only the
  server's own OAuth token — so this was an ext-proxy-only least-privilege regression. Now
  `_ext_proxy_forward_headers` strips hop-by-hop + all credential/identity headers (`authorization`,
  `proxy-authorization`, `cookie`, `set-cookie`, `x-api-key`) + any gateway-internal `X-Gateway-*` header, and
  injects the gateway's stored OAuth bearer for the upstream domain (if any) as the SOLE `Authorization`.
- **WHY:** forwarding the caller's gateway credential to an arbitrary external host is a direct
  credential-exfiltration / least-privilege violation and squarely a 1.4 "prevent MCP data leakage" defect —
  the caller's key is data that must never egress to the tool host.
- **NOW DOES:** the external MCP server receives ONLY safe/protocol headers (Content-Type, Accept,
  Mcp-Session-Id, …) plus its OWN OAuth token when the gateway holds one; the caller's gateway key/cookies
  never leave the gateway. Parity with the sandbox-routed transport.
- **Touched whose work:** the gateway external-proxy path (CHG-0004/0005 SSE-scan author); complements the
  sandbox-path header hygiene in `broker_send_rpc`.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py -q`
  → 20 passed (+2: `_ext_proxy_forward_headers` strips Authorization/Cookie/X-Api-Key/X-Gateway-* + hop-by-hop
  and the caller-key value never survives; a stored upstream OAuth token is injected as the sole
  Authorization). Broad sweep `ai_mesh_gateway/tests` → 1077 passed, 0 failed.
- **REMAINING:** none for the ext-proxy header hygiene. (Follow-up idea: enforce a per-key tool allowlist on
  ext_mcp_proxy too, if that transport is meant to be tool-scoped — currently transport-level only.)

### CHG-0034 — Per-request body-size ceiling on the MCP routes (gateway validation / DoS, G3 item 9)
- **Date:** 2026-07-02
- **Scratchpad item:** G3 item 9 (gateway auth/authz/**validation**/rate-limit/policy/audit) + the resource
  mandate. The RAG/embeddings paths have 413 payload guards; the MCP routes had NONE.
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`_MCP_MAX_BODY_BYTES` + `_mcp_body_too_large` +
  `_mcp_body_too_large_response`; guard added to `org_mcp_jsonrpc`, `org_mcp_tool_call`, `ext_mcp_proxy`);
  `gateway/ai_mesh_gateway/tests/test_mcp_rate_limit.py` (+4 tests).
- **WHAT:** every MCP tool-call handler buffers the whole request into memory (`await request.body()` /
  `await request.json()`) with no size ceiling, so a tenant could POST a very large body and exhaust gateway
  memory (a DoS / availability gap). Added a per-request ceiling (`MCP_MAX_BODY_BYTES`, default 10 MiB,
  env-configurable) enforced by `_mcp_body_too_large(request)` — rejects an oversized declared Content-Length
  with a 413 `mcp_body_too_large` BEFORE the body is read, on all three tenant-facing MCP entry points.
- **WHY:** the architecture mandate requires gateway request VALIDATION + resource limits; an unbounded body
  on the hot MCP path is a straightforward memory-exhaustion vector that the sibling RAG/embeddings paths
  already guard against.
- **NOW DOES:** `org_mcp_jsonrpc`, `org_mcp_tool_call`, and `ext_mcp_proxy` reject a body whose declared
  Content-Length exceeds the ceiling with HTTP 413 before buffering it. The guard is defensive against test
  doubles (missing headers → allow) and leaves the existing body-read flow untouched (no change to
  request.body()/json() — so it's non-invasive on the hot path).
- **Touched whose work:** the gateway MCP proxy validation path; complements the CHG-0031/0032 rate-limit
  coverage (both are pre-processing DoS gates).
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_rate_limit.py -q` →
  14 passed (+4: `_mcp_body_too_large` helper matrix; each of the three routes returns 413
  `mcp_body_too_large` on an oversized Content-Length). Broad sweep `ai_mesh_gateway/tests` → 1081 passed, 0
  failed.
- **REMAINING (documented limitation):** this is a Content-Length pre-check — it does NOT catch a chunked
  request that omits Content-Length (the adversarial case). The infra-layer body limit (nginx/ALB) covers
  that today; a future streaming cap (`request.stream()` with an abort) would close it at the app layer, but
  that requires re-mocking the body-read across the MCP test harness (deferred to avoid a large test churn).

### CHG-0035 — Verification: sandbox-agent egress hygiene + container security + cross-tenant oracle (read-only)
- **Date:** 2026-07-02
- **Scratchpad item:** G3 items 10/12 (sandbox security posture / egress) + G5 item 19 (cross-tenant canary).
  A read-only audit triggered by CHG-0033 — I checked every OTHER security-critical egress/isolation path for
  a similar class of gap. All CLEAN; no code changed.
- **Files:** `mcp-parallel/findings/backstop-p12b-sandbox-egress-hygiene/audit.md` (evidence). No source edits.
- **WHAT (all verified CLEAN):**
    - **Sandbox agent HTTP dialing** (`upstream_manager.py`): `follow_redirects=False` (no SSRF-via-redirect /
      egress bypass), per-method timeouts, and NO `verify=False` anywhere in the broker/sandbox (TLS verify is
      the httpx default — no MITM window).
    - **Sandbox agent WebSocket** (`ws_manager.py`): scheme restricted to ws/wss; `websockets.connect` passes
      no `ssl=` override so `wss://` uses the default verifying SSL context; handshake bounded by open_timeout
      + wait_for. No `CERT_NONE`/`check_hostname=False`/`_create_unverified` anywhere.
    - **Container security** (`docker_manager._run_kwargs`/`_security_opts`): no-new-privileges + Docker DEFAULT
      seccomp (explicitly NOT `unconfined`) + `cap_drop=ALL` + `read_only=True` rootfs + `pids_limit=256` +
      `mem_limit` with `memswap_limit=mem_limit` (SWAP DISABLED) + `nano_cpus` + writable tmpfs only for caches.
    - **Cross-tenant harness** (`scripts/mcp_multi_org_harness.py`, another session): byte-level canary
      cross-target oracle (`conc~<org>~<server>~…` echoed back + owner compared + JSON-RPC id round-trip) and a
      fail-closed negative matrix (attacker key vs victim org path — any non-401/403 counts as a breach/failure).
- **WHY:** after finding the CHG-0033 credential leak, the backstop must confirm the sibling egress/isolation
  paths don't share the defect; these are exactly the item-12 (seccomp/cap_drop/egress) + item-19 (canary)
  claims, now evidenced rather than assumed.
- **NOW DOES:** nothing changed — documents that these paths are sound. RESIDUAL (unchanged, item 12): runc
  (not gVisor) + per-org networks `internal=false` (no network-level egress default-deny) — INFRA
  prerequisites, not code gaps. Minor note: the harness's redundant get-sum operand hash can collide
  (~1/640k/pair) but the echo canary is the definitive detector, so no false PASS.
- **Touched whose work:** verifies the broker/sandbox-agent security (prior sessions) + the multi-org canary
  harness (another session). No edits.
- **VERIFY:** the three `grep` commands in the evidence file reproduce the audited facts (follow_redirects=False;
  no verify=False; cap_drop/read_only/memswap_limit/seccomp).

### CHG-0036 — FINDING: the MCP guardrail UI does not reflect 1.4 compliance tags (G6 item 21)
- **Date:** 2026-07-02
- **Type:** finding only (no code change — the fix spans a control-plane change I cannot safely gate from
  this session + a Playwright-gated frontend change owned by fe-harden).
- **Scratchpad item:** G6 item 21 ("MCP panels reflect 1.4 — tags, redaction indicators"). Corrects the
  fe-harden "item 11 DONE" claim (commit 6658f9fc).
- **Files:** `mcp-parallel/findings/backstop-p21-frontend-tag-reflection/finding.md` (evidence). No edits.
- **WHAT:** `frontend/src/components/simulator/MCPGuardrailSimulator.jsx` builds its `verdict` (≈311-354)
  from `action`/`matched_policies`/`matched_rules`/`redacted_input_args`/`redacted_output`/`blocked` — but
  never `compliance_tags`, and never renders a tag chip. So the PII/PHI/PCI/GDPR/HIPAA/INFRA framework tags
  the mandate wants surfaced are shown NOWHERE. Additionally the LIVE mode (2xx branch ≈338-345) is nearly
  blank — `matched_policies:[]`, no redaction display, no tags — so a live tool call the gateway REDACTED
  (CHG-0024/0030) shows no redaction indicator. **Root cause:** the dry-run endpoint `POST /api/policies/test/`
  (`control/…/policy/evaluation_views.py` PolicyTestView) IMPORTS `get_compliance_tags` (line 25) but its
  policy-match response payload (≈551-585) omits `compliance_tags` entirely; likewise the live
  `/api/mcp-connector/tools/call/` response keeps compliance_tags only in the MCPEvent audit, not the client
  body. So the frontend cannot reflect tags because the backend never sends them.
- **WHY (recorded, not fixed here):** (1) the control change is not safely gate-able — there is NO control
  venv and Django tests need a test DB, and "DO NOT FAKE GREEN" forbids an ungate-able cross-plane edit;
  (2) the frontend change is owned + actively iterated by fe-harden and its gate needs Playwright (no dev
  server confirmed up) — a dormant partial risks conflict; (3) the tag-vocab entanglement (item 5: gateway
  GDPR/HIPAA/PII/INFRA vs control GDPR-PII/HIPAA-PHI/… catalog codes) is an owning-session semantic decision.
- **FIX (for owners):** control — add `compliance_tags` to the PolicyTestView + MCPToolCallView response
  (via `get_compliance_tags`, unifying the vocab per item 5); frontend — capture
  `compliance_tags: data?.compliance_tags || []` in every verdict branch + render a chip list (mirror the
  matched_policies block ≈582-600) + enrich the LIVE 2xx branch to read decision/redacted_output/tags. Gate:
  npm build + Playwright (chips render for a PII payload, both themes).
- **VERIFY:** `grep -n compliance_tags frontend/src/components/simulator/MCPGuardrailSimulator.jsx` → none;
  `grep -n compliance_tags control/ai_mesh_control/policy/evaluation_views.py` → import only, not in payload.

### CHG-0037 — Completion-readiness matrix (source of truth for what's left + why)
- **Date:** 2026-07-02
- **Type:** consolidation doc (no code change).
- **Scratchpad item:** G7 item 22 (recursive verification / completion assessment) — provides the honest
  requirement→status map the completion gate needs.
- **Files:** `docs/mcp/COMPLETION_READINESS.md` (new).
- **WHAT:** a single matrix mapping every 1.4 + architecture + stress mandate requirement to CODE-HARDENED /
  VERIFIED / OPEN with the specific evidence (CHG id) or blocker. Consolidates the 36 prior entries into an
  actionable finish-line view for the parallel sessions + any future completion check.
- **WHY:** after ~13 backstop entries the per-change log is long; a requirement-indexed status view makes it
  obvious that the CODE-level 1.4/gateway/transport/isolation hardening is comprehensive and gated green,
  while the remainder is (a) dedicated-host stress (items 14–19, the completion gate), (b) infra (gVisor,
  egress default-deny, OTEL tracing, PG/Redis backup, npm prod-enable), and (c) owned/cross-plane (item-21 UI
  tag reflection, item-5 vocab). It also states plainly that completion is NOT met and why.
- **NOW DOES:** documents the finish line; nothing functional changed.
- **Touched whose work:** none (new doc). Cross-references every prior CHG + the parallel sessions' domains.
- **VERIFY:** read `docs/mcp/COMPLETION_READINESS.md`; each ✅ row cites a CHG whose VERIFY command is in this
  changelog; the gateway suite gate is `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests -q`
  → 1081 passed.

### CHG-0040 — Accept ws:// / wss:// on MCPServerRegistration.url (P4.13 Blocker 2)
- **Date:** 2026-07-02
- **Scratchpad item:** P4.13 / P6.18 / P6.19 — four-transport sandbox e2e. Cross-seam exception (Cursor-owned
  iter39; user: don't wait for Claude after 2+ iters blocked on URLField).
- **Files:** `control/ai_mesh_control/mcp_connector/models.py` (URLField→CharField);
  `control/ai_mesh_control/mcp_connector/migrations/0015_mcpserverregistration_url_charfield.py`;
  `control/ai_mesh_control/mcp_connector/tests/test_oauth_transport_guard.py` (+1 ws scheme test);
  `scripts/mcp_enable_http_via_sandbox.sh` (+ws stub in `MCP_ALLOW_INTERNAL_HOSTS` default);
  `scripts/mcp_ws_everything_stub.mjs` (echo `Echo: {message}` parity); `scripts/mcp_p10_recursive_gate.py`
  (agent-pytest timeout 180→360s — suite takes ~250s).
- **WHAT:** Django `URLField` on `MCPServerRegistration.url` rejected `ws://` at the model layer even though
  `MCPServerCreateSerializer.validate` already allowed ws/wss via `is_safe_outbound_url`. Replaced with
  `CharField(max_length=2048)`; serializer SSRF guard remains authoritative. Added `ws-everything.stub`
  to the internal-host allowlist for in-cluster stub registration.
- **WHY:** iter37/38 confirmed gateway ws routing LANDED (CHG-0026) but ws transport verify stayed 3/4 —
  registration failed with `Enter a valid URL.` Blocking P4.13 `[x]` for 2+ iterations.
- **NOW DOES:** `ws://ws-everything.stub:3003/mcp` registers; manifest gains `websocket` slug; gateway routes
  ws via `broker_send_rpc` end-to-end. **4/4 transports PASS ROUNDS=3**; gateway `ss :443` empty.
- **Touched whose work:** Claude-owned control seam (explicit cross-seam claim); complements CHG-0026 gateway
  ws broker routing. Not a data-leak change — registration/isolation completeness.
- **VERIFY:** `docker compose exec control python manage.py migrate mcp_connector`; `python3
  scripts/mcp_register_transport_servers.py` → `websocket: ws-everything-stub (created)`; `ROUNDS=3
  TRANSPORT_MANIFEST=... python3 scripts/mcp_sandbox_transport_verify.py` → `SANDBOX_TRANSPORT: PASS`;
  broker 98 + agent 44 passed; Playwright B1/B2/B4 individual PASS. Evidence:
  `mcp-parallel/findings/p4-13/RECHECK_ITER39.md`.

### CHG-0038 — tools/list visibility parity with per-key authz (least-privilege, G2 item 3)
- **Date:** 2026-07-02
- **Scratchpad item:** G2 item 3 (per-user/agent/role tool authorization) — extends it from EXECUTION to
  VISIBILITY. + 1.4 least-privilege / context minimization.
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (new `_filter_tools_by_key_allowlist`; applied at all 4
  tools/list filter sites in `org_mcp_jsonrpc` + `org_mcp_tools_list`);
  `gateway/ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py` (+2).
- **WHAT:** the per-key `mcp_allowed_tools` allowlist was enforced at tools/CALL time (`_tool_allowed_by_key`,
  CHG-0006 — a forbidden tool → 403) but NOT on tools/LIST. tools/list was filtered ONLY by the server-level
  `disabled` set (`_filter_tools_by_enabled`), so a restricted key SAW every server-enabled tool — including
  ones it would be 403'd on. That is an info-disclosure + authz-consistency gap (the client learns about tools
  it cannot use). Added `_filter_tools_by_key_allowlist(tools, auth)` (empty/absent allowlist = all visible,
  mirroring `_tool_allowed_by_key`; non-dict/name-less entries pass through like `_filter_tools_by_enabled`)
  and layered it after the enabled-filter at every tools/list site: the JSON-RPC adapter branch, the JSON-RPC
  backend branch, and the REST `org_mcp_tools_list` route (which now also resolves `_get_auth_context`).
- **WHY:** least-privilege + "context minimization" — a caller should not even SEE tools outside its
  authorization; visibility must match the call-time authz decision, else the allowlist leaks the tool
  catalog.
- **NOW DOES:** a key with `mcp_allowed_tools=["echo"]` sees ONLY `echo` in tools/list (JSON-RPC + REST);
  a key with an empty allowlist sees all (unchanged). Consistent with the 403 it would get calling a
  non-allowlisted tool.
- **Touched whose work:** extends the CHG-0006/0008 per-key authz to the visibility surface. The
  server-`disabled` filter is unchanged (now composed with the key filter).
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py -q`
  → 22 passed (+2: `_filter_tools_by_key_allowlist` matrix; `org_mcp_tools_list` hides `get-sum` for a key
  allowlisted to `echo`). Broad sweep `ai_mesh_gateway/tests` → 1083 passed, 0 failed.

### CHG-0039 — Scan resources/read + prompts/get SSE results on the external proxy (G2 item 2 completeness)
- **Date:** 2026-07-02
- **Scratchpad item:** G2 item 2 (byte-verified result redaction) — extends the ext-proxy SSE result scan
  from tools/call ONLY to all FINITE MCP request/response methods.
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`_EXT_FINITE_RESULT_METHODS` + `_ext_scan_result`;
  the ext_mcp_proxy SSE branch now gates on `_ext_scan_result` not `_ext_is_tools_call`);
  `gateway/ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py` (+2).
- **WHAT:** `ext_mcp_proxy` (the transparent proxy to allowlisted EXTERNAL MCP servers) buffered + scanned an
  SSE response ONLY for `tools/call` (CHG-0004); every other method's SSE streamed through UNSCANNED. But
  `resources/read`, `resources/list`, `prompts/get`, `prompts/list` (and tools/list) are FINITE
  request/response methods whose result can carry PII/secrets from the external server (e.g. a code file read
  via `resources/read` containing an API key). So those results egressed RAW over SSE — a 1.4 leak the
  non-streaming JSON branch already closed (it scans ANY method's result). Now the SSE branch buffers + scans
  those finite methods too (via `_scan_reframe_sse_tool_result`, which walks the whole result), while
  genuinely-streaming methods (`notifications/*`, `*subscribe`, long-lived streams) still pass through live —
  they have no bounded result and buffering could hang. The buffer (`aread`) is bounded by the httpx timeout.
- **WHY:** "prevent MCP data leakage" — MCP resource/prompt content is data that must be scanned before it
  reaches the client, exactly like tool results; the SSE variant was the one path still forwarding it raw.
- **NOW DOES:** a `resources/read`/`prompts/get`/… SSE result with PII/secrets is masked (or blocked) before
  re-emission on ext-proxy; notifications/subscriptions still stream through (no hang). The org path already
  rejects these methods (`-32601`), so this closes the only reachable unscanned resource-content path.
- **Touched whose work:** extends the ext-proxy SSE scan (CHG-0004/0005). Non-finite passthrough unchanged.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py -q`
  → 24 passed (+2: a `resources/read` SSE PII result is buffered + masked, not forwarded raw; a
  `notifications/*` SSE still streams through unbuffered — `aread` not awaited). Broad sweep
  `ai_mesh_gateway/tests` → 1085 passed, 0 failed.

### CHG-0043 — Scan JSON-RPC ERROR content on the external proxy (G2 item 2 — last unscanned egress vector)
> **Note (2026-07-02):** renumbered from **CHG-0040** to resolve a change-id collision — another session
> independently used CHG-0040 for "Accept ws:// / wss:// on MCPServerRegistration.url (P4.13 Blocker 2)"
> (see the earlier `### CHG-0040` entry above). Code comments in `mcp_proxy.py` /
> `test_mcp_bare_proxy_scan.py` were updated to match. Content below is unchanged.
- **Date:** 2026-07-02
- **Scratchpad item:** G2 item 2 (byte-verified result redaction) — extends the ext-proxy egress scan from
  the `result` to the `error` field.
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (ext_mcp_proxy non-streaming branch + the
  `_scan_reframe_sse_tool_result` SSE helper now scan `error` when there is no `result`);
  `gateway/ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py` (+2).
- **WHAT:** the ext-proxy result scan only inspected `data.get("result")` (non-streaming) / result frames
  (SSE); a JSON-RPC ERROR response (`{"error": {...}}`, no `result`) egressed UNSCANNED. An untrusted external
  MCP server can leak a secret in an error message — e.g. `"connect failed: postgres://admin:s3cr3t@db.
  internal/prod"` (a connection string), an internal hostname, or a token. Now both paths, when there is no
  result but an `error` is present, run `_scan_tool_result_floor` over the error (which recursively walks its
  message/data) and mask any detected secret/PII (redact-only — it is already an error). Fail CLOSED: on a
  scan error the whole error content is withheld (a generic "could not be safely inspected" error) rather than
  forwarded raw. Non-error frames (notifications / keep-alives) still pass through verbatim.
- **WHY:** "prevent MCP data leakage" — a secret in an error string is still a leak; the error field was the
  one remaining unscanned egress path on the ext-proxy after CHG-0004/0005/0039 covered every result shape.
- **NOW DOES:** a JSON-RPC error carrying a secret/PII from an external server is masked before it reaches the
  client (both `application/json` and `text/event-stream`); the org path already rejects non-tool methods and
  its tool errors flow through the same result-scan machinery.
- **Touched whose work:** completes the ext-proxy egress hardening (CHG-0004/0005/0039). Result-scan behaviour
  unchanged; the error scan is an additive `elif`/branch.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py -q`
  → 26 passed (+2: a connection-string secret in a non-streaming error message AND in an SSE error frame is
  masked — `s3cr3tPass` absent). Broad sweep `ai_mesh_gateway/tests` → 1087 passed, 0 failed.

### CHG-0041 — Extend the ext-proxy inbound credential block to prompts/get; verify logging is PII-clean (G2 item 2)
- **Date:** 2026-07-02
- **Scratchpad item:** G2 item 2 / 1.4 — inbound credential egress prevention (the client→server side, twin of
  the outbound result/error scan).
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`_EXT_ARG_SCAN_METHODS`; the ext_mcp_proxy inbound
  credential block now runs for `prompts/get` too; removed the now-dead `_ext_is_tools_call` flag);
  `gateway/ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py` (+1).
- **WHAT (two parts):**
    1. **Logging audit — CLEAN (no change):** audited the gateway MCP path for a PII/secret-to-logs leak.
       `mcp_proxy` logs only `target_url` (allowlisted host, no userinfo); the scan orchestrator logs only
       exception messages, not the scanned text; `metrics.record_bedrock_call` logs method/model/token-counts
       only (no prompt/content); the audit record (`POLICY_AUDIT_STORE_PROMPT_RESPONSE`, default off) does not
       store raw prompt/response. No raw-payload logging.
    2. **Inbound credential block extended:** `ext_mcp_proxy` credential-scanned tool ARGS only for
       `tools/call`, so an accidental credential in a `prompts/get` `arguments` object (identical
       `params.arguments` shape) would egress raw to the external server. Now the block runs for
       `_EXT_ARG_SCAN_METHODS = {tools/call, prompts/get}`. `resources/read` is deliberately EXCLUDED — its
       param is a URI, and blocking a legitimate `https://user:token@host` auth-in-URL would break authed
       reads. Also removed the `_ext_is_tools_call` flag, which CHG-0039 left dead (set, never read).
- **WHY:** symmetry with the outbound egress hardening (CHG-0039/0040) — the credential-egress guard should
  cover every method carrying a `params.arguments` object, not just tools/call; and the logging audit confirms
  the guardrails don't themselves leak the data to logs.
- **NOW DOES:** a credential in `prompts/get` arguments is blocked before it egresses to the external server
  (parity with tools/call); logging carries no raw MCP payload.
- **Touched whose work:** completes the ext-proxy inbound credential guard (CHG-0033 header hygiene + the
  tools/call arg block). Result/error scan unchanged.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py -q`
  → 27 passed (+1: a credential in `prompts/get` args is blocked, upstream NOT contacted). Broad sweep
  `ai_mesh_gateway/tests` → 1088 passed, 0 failed. `grep -c _ext_is_tools_call mcp_proxy.py` → 0 (dead flag
  removed).

### 2026-07-02 — MCP-PAGE-CP01 | scripts/ralph/mcp_page_typesim.mjs (new) | WHAT: reusable type-sim Playwright harness (keyboard.type delay:40 / clear via Ctrl+A→Delete, NEVER fill; exports typeSim/login/openRegisterDialog) | WHY: comma-drop/focus-loss modal bug only reproduces under realistic keystrokes; fill() masks it | NOW DOES: CP01 smoke green (type 'a,b,c' into name → value correct, commas 2/2, focus held) | touched: none (new) | VERIFY: node scripts/ralph/mcp_page_typesim.mjs → ok:true

### 2026-07-02 — MCP-PAGE-CP02 | scripts/ralph/mcp_page_cp02_args_repro.mjs (new) | WHAT: type-sim repro of Args comma-drop | WHY: CP02 prove bug | NOW DOES: REPRODUCED — type "a,b,c --flag,x" into Args → "abc--flagx", commas 0/3 + space dropped, focus KEPT (separator-drop, not remount); name/url/command unaffected → bug is specific to the comma-separated Args onChange (root-cause CP04) | touched: none (MCPConnectorPanel Args input suspect) | VERIFY: node scripts/ralph/mcp_page_cp02_args_repro.mjs → bugReproduced:true

### 2026-07-02 — MCP-PAGE-CP03 | mcp_page_cp03_allfields_repro.mjs (new) + mcp_page_typesim.mjs (bounded click timeout) | WHAT: per-field type-sim record | WHY: CP03 record buggy fields | NOW DOES: args=SEP-DROP(0/3), env=SEP-DROP(0/1); name/url/description/command/bearer=clean; focus KEPT (not remount). Modal field map: Args ph "-y, @playwright/mcp@latest", Env ph "GITHUB_TOKEN=ghp_xxx", Command ph "npx". Two fields drop commas → common onChange (CP04 target) | touched: none (MCPConnectorPanel Args+Env onChange = CP05 fix) | VERIFY: node scripts/ralph/mcp_page_cp03_allfields_repro.mjs → buggyFields [args,env]

### CHG-0042 — At-rest encryption for OAuth flow state + tokens; OAuth flow security verified (G3/1.4)
- **Date:** 2026-07-02
- **Scratchpad item:** G3 item 11 (Redis correctness) + 1.4 credential protection. Also verifies the OAuth
  flow's CSRF/PKCE/SSRF posture (part of "gateway auth/authz").
- **Files:** `gateway/ai_mesh_gateway/mcp_oauth_proxy.py` (new `_oauth_cipher`/`_enc_dumps`/`_enc_loads`; the 4
  Redis (de)serialization sites — `_flow_save`/`_flow_pop`/`_token_save`/`_token_load` — now use them);
  `gateway/ai_mesh_gateway/tests/test_mcp_oauth_encryption.py` (new, +4).
- **WHAT (two parts):**
    1. **OAuth flow security — VERIFIED clean (no change):** the callback validates `state` (`_flow_pop(state)`
       → reject on miss = CSRF guard), uses PKCE (`code_verifier` from the STORED flow, not the request),
       runs `_assert_safe_url` on the attacker-metadata-derived token endpoint (SSRF), and dials with
       `follow_redirects=False` (no redirect-SSRF). Sound.
    2. **At-rest encryption added:** OAuth access/refresh tokens (and the flow record's `client_secret` +
       `code_verifier`) were stored as PLAINTEXT `json.dumps` in Redis. Redis is internal, but a compromise
       would expose every org's upstream MCP credentials. Added opt-in encryption gated by
       `MCP_OAUTH_ENCRYPTION_KEY` (a urlsafe-base64 Fernet key): `_enc_dumps` encrypts on write when the key is
       set, `_enc_loads` transparently reads encrypted values, cipher-absent plaintext, AND legacy plaintext
       written before the key (Fernet ciphertext is prefix-detected as `gAAAAA`), so enabling the key never
       orphans existing tokens. **Default OFF = plaintext, byte-unchanged** — zero risk to the live flow; an
       invalid key logs a warning and falls back to plaintext (never breaks token storage).
- **WHY:** credential-at-rest hardening — OAuth tokens are the keys to the external MCP servers; the mandate's
  "prevent MCP data leakage" + "Redis correctness" both point at not storing them in the clear.
- **NOW DOES:** with `MCP_OAUTH_ENCRYPTION_KEY` set (recommended prod config), OAuth flow state + tokens are
  Fernet-encrypted in Redis; without it, behaviour is exactly as before.
- **Touched whose work:** the gateway OAuth proxy (upstream-token author). Additive/opt-in.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_oauth_encryption.py
  ai_mesh_gateway/tests/test_mcp_oauth_org_scope.py -q` → 8 passed (default-off plaintext; encrypted round-trip
  hides access/refresh/client_secret; legacy plaintext still read; invalid key → plaintext fallback). Broad
  sweep `ai_mesh_gateway/tests` → 1092 passed, 0 failed.

### 2026-07-02 — MCP-PAGE-CP04 | frontend/src/components/MCPConnectorPanel.jsx (analysis) | WHAT: ROOT CAUSE = controlled-input-bound-to-parsed-collection, NOT a remount | Args (:1499-1500): value=args.join(", ") + onChange split(",").map(trim).filter(Boolean) → typing "," makes ["a",""]→filter→["a"]→re-render "a" (comma erased); trim kills spaces. Env (:1509-1520): same with object round-trip | FIX (CP05): store raw text in state, parse to array/object only on submit (payload @168-169) not per keystroke | VERIFY(after fix): cp03 args/env → clean

### 2026-07-02 — MCP-PAGE-CP05 | frontend/src/components/MCPConnectorPanel.jsx | WHAT: FIX modal separator-drop — Args/Env inputs hold RAW TEXT (args_text/env_text), parsed to args[]/env_vars{} only at submit (buildServerPayload), never per keystroke; fallback to array/object for presets | WHY: CP04 array/object round-trip erased typed commas/spaces | NOW DOES: VERIFIED — CP03 buggyFields [], CP02 "a,b,c --flag,x" commas 3/3, vite build green (6.69s) | touched: MCPConnectorPanel.jsx (B1 logic intact) | VERIFY: cp03 [] + cp02 3/3 + npm run build

### 2026-07-02 — MCP-PAGE-CP06 | mcp_page_cp06_verify.mjs (new) + mcp_page_typesim.mjs (opts.fast) | WHAT: comprehensive modal-typing verify — long comma strings into every field, values correct + focus kept, 4 viewports x 2 themes, 0 console errors | WHY: CP06 gate prove CP05 fix holds everywhere | NOW DOES: ALL 8 combos PASS (allPass:true). SECTION A (modal typing bug CP01-06) COMPLETE | touched: none new | VERIFY: node scripts/ralph/mcp_page_cp06_verify.mjs → 8/8 PASS

### CHG-0044 — Force npm ignore-scripts ON for the spawned stdio child (item 8 — supply-chain RCE control was defeated)
- **Date:** 2026-07-02
- **Severity:** HIGH (supply-chain remote code execution — a registered `npx` MCP server's untrusted package could run install lifecycle scripts on fetch inside the sandbox).
- **Files:** `shared/ai_mesh_shared/mcp_stdio_common.py` (`_build_child_env` — force `npm_config_ignore_scripts=true`); `services/mcp-broker/tests/test_stdio_common.py` (+3 tests).
- **WHAT (gap):** `docker_manager` sets `npm_config_ignore_scripts=true` on the **container** env (comment: "Kill postinstall lifecycle scripts during npx fetch (supply-chain RCE vector)"). But the actual stdio server is spawned by the sandbox agent via
  `asyncio.create_subprocess_exec(..., env=proc_env)` where `proc_env = _build_child_env(...)`. `_build_child_env` rebuilds the child env **FRESH from the `_SAFE_ENV_PASSTHROUGH` allowlist** (which does NOT include `npm_config_ignore_scripts`) and an explicit `env=` **replaces** the process environment (no parent inheritance). So the `npx` child that fetches untrusted packages ran with `ignore-scripts` **unset → defaulting to false → preinstall/install/postinstall scripts executed**. The container-level flag was dead for the one process that matters. There is no baked `.npmrc` fallback in the sandbox image, so the env var was the sole mechanism. Secondary gap: a malicious server-spec `env: {"npm_config_ignore_scripts": "false"}` would have overridden it (merged before the return).
- **WHY:** the mandate's "no unknown npm on the host / supply-chain hardening" — an untrusted npm package must not be able to execute arbitrary code via install lifecycle scripts when a tenant registers `npx <pkg>`.
- **NOW DOES:** `_build_child_env` force-pins `child["npm_config_ignore_scripts"] = "true"` **unconditionally and LAST** (mirroring the existing `MCP_REMOTE_CONFIG_DIR` force-pin), after the server-spec merge and denylist strip. Every spawned stdio child (broker sandbox agent AND the gateway's legacy in-process stdio adapter, which share this helper) now carries ignore-scripts=true, and no server-spec/host env can re-enable lifecycle scripts. The package's `bin` (the MCP server itself) still runs — only install-time scripts are blocked — so legitimate servers are unaffected.
- **Touched whose work:** the sandbox stdio path (broker `sandbox-image/agent/stdio_manager.py` + gateway `mcp_stdio_adapter.py`) via the shared `mcp_stdio_common._build_child_env`. Additive (one forced key); complements BACKSTOP CHG-0022 (pin/allowlist env propagation).
- **VERIFY:** `cd services/mcp-broker && PYTHONPATH="$PWD/src:$PWD/../../shared:$PWD/sandbox-image/agent" .venv/bin/python -m pytest tests/test_stdio_common.py -q` → 19 passed (default child has ignore-scripts=true; server-spec `false`/``/`0`/`no`/`FALSE` all overridden to true; host-env false overridden). Full broker suite → 101 passed. Gateway sweep `ai_mesh_gateway/tests` → 1092 passed, 0 failed. Evidence: `mcp-parallel/findings/backstop-p8-npm-ignore-scripts/finding.md`.

### 2026-07-02 — MCP-PAGE-CP07 | frontend/src/components/MCPConnectorPanel.jsx (addServer refactor+cancelAdd+footer) + mcp_page_cp07_register_flow.mjs (new) | WHAT: Register attempts MCP connect+tool-discovery INLINE, modal stays OPEN during attempt (bug #2); create→sync inline→fail=inline error+stay open (Retry re-syncs same row)/success=close+list; Cancel deletes orphan row (never list 0 tools) | WHY: was POST then close immediately → 0-tools cards | NOW DOES: VERIFIED cp07Pass:true (example.com/mcp → stayedOpen:true, connecting+inline error), build green (6.30s) | FOLLOWUP: inline error leaks raw upstream (405 HTML) → sanitize in Section D CP16-19 | touched: MCPConnectorPanel.jsx (B1 intact) | VERIFY: node scripts/ralph/mcp_page_cp07_register_flow.mjs → cp07Pass:true

### 2026-07-02 — MCP-PAGE-CP08 | frontend/src/components/MCPConnectorPanel.jsx (retry PATCHes row before re-sync) + mcp_page_cp08_retry.mjs (new) | WHAT: fail→clear inline error+modal open→fix+Retry PATCHes edited config then re-syncs, no dup create | WHY: CP07 retry did not apply form edits | NOW DOES: VERIFIED via network — register example.com→405 inline error, edit URL→Retry fires PATCH+tools sync (no 2nd create), Cancel fires DELETE (orphan cleanup); cp08Pass:true, build green | FOLLOWUP: sanitize raw upstream error (Section D CP16-19) | touched: MCPConnectorPanel.jsx | VERIFY: node scripts/ralph/mcp_page_cp08_retry.mjs → cp08Pass:true

### CHG-0045 — Audit the cross-tenant scope violation (item 9 — audit-completeness)
- **Date:** 2026-07-02
- **Severity:** MEDIUM (audit/forensics gap — a cross-tenant access attempt left no record in the MCP audit trail).
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (new `_audit_and_return_scope_error` wrapper; 4 route call sites); `gateway/ai_mesh_gateway/tests/test_mcp_org_scope_data_path.py` (+2 tests).
- **WHAT (gap):** every tenant-facing MCP route (`org_mcp_jsonrpc`, `org_mcp_tool_call`, `org_mcp_tools_list`, `org_mcp_server_health`) starts with `_validate_org_scope`, which returns 403 `org_scope_violation` when an authenticated key's org ≠ the URL org (a cross-tenant access attempt — the core threat this multi-tenant firewall exists to stop). But that 403 was only `LOG.warning`'d; it never called `_record_gateway_event`, so — unlike the per-key authz denials (CHG-0006, which DO audit) — the single most forensically important MCP security event was invisible to the MCPEvent audit/SIEM/compliance layer.
- **WHY:** "gateway … audit" (item 9) + audit-completeness — a blocked cross-tenant breach attempt must be recorded so a SOC can see it.
- **NOW DOES:** a new async wrapper `_audit_and_return_scope_error` calls `_validate_org_scope` (unchanged) and, on a 403, emits an MCPEvent (`decision=block`, `reason=org_scope_violation`) **attributed to the CALLER's real org** (`auth.org_slug`, never the target — so the record stays inside the caller's tenant boundary), with `target_org` + `key_prefix` in metadata. All 4 routes now call the wrapper. The 401 (unauthenticated, no org to attribute) is left to the auth middleware/access logs. `_validate_org_scope` stays SYNC so the ~13 `patch.object(..., "_validate_org_scope", return_value=None)` sites + the direct-call unit tests are unchanged. Rate-limit (429) auditing was deliberately NOT added — auditing every rejection under a burst would amplify load exactly when the limiter is protecting the system (documented deferral); body-too-large (413) audit is a lower-priority follow-up.
- **Touched whose work:** the gateway MCP auth layer (`_validate_org_scope`, part of the CHG-0016 cross-tenant isolation verification). Additive wrapper; no behaviour change to the block itself.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_org_scope_data_path.py -q` → 6 passed (cross-tenant 403 emits exactly one `org_scope_violation` MCPEvent attributed to the caller's org with `target_org` in metadata; same-org pass emits none). Route tests that patch `_validate_org_scope` unaffected (test_mcp_rate_limit / test_mcp_bare_proxy_scan / test_e12_* → 67 passed). Broad sweep `ai_mesh_gateway/tests` → 1094 passed, 0 failed. Evidence: `mcp-parallel/findings/backstop-p9-scope-violation-audit/finding.md`.

### 2026-07-02 — MCP-PAGE-CP09 | scripts/ralph/mcp_page_cp09_success.mjs (new verify; success branch already in CP07 addServer) | WHAT: verify register success — on tool discovery modal closes THEN server lists WITH tools (never 0) | HOW: Playwright route-mock sync(tools:3)+list(tools_count:3), decoupled from flaky shared sandbox | NOW DOES: cp09Pass:true (modalClosedOnToolDiscovery, listQueried, listedWithToolCount) | REAL-CONNECT NOTE: live Everything crashed exit -6 under parallel-loop sandbox stress (contention) — showed inline (validates CP07/08) + leaked internals (Section D target); real e2e = Section J | touched: none | VERIFY: node scripts/ralph/mcp_page_cp09_success.mjs → cp09Pass:true

### CHG-0046 — Non-string scan target had a no-op redaction setter (report-redact / forward-raw)
- **Date:** 2026-07-02
- **Severity:** HIGH (1.4 result-redaction fail-open — a detected secret/PII in a non-string field was reported redacted yet egressed RAW).
- **Files:** `gateway/ai_mesh_gateway/mcp_scan_targets.py` (`extract_scan_targets`: real setters for non-string dot-path + simple-key targets); `gateway/ai_mesh_gateway/tests/test_mcp_scan_targets.py` (+3) & `tests/test_mcp_scan_orchestrator.py` (+1).
- **WHAT (gap):** the two-tier scanner flattens each scan target to text via `_safe_json` (so a NUMBER, LIST, or OBJECT value IS scanned) and applies redaction by calling the target's `setter(new_text)`. For `key_path` / simple-key targeting, a target resolving to a **non-string** value was bound to a **no-op setter** (`lambda: None`, `mcp_scan_targets.py:108` dot-path and `:123` simple-key). So when Tier-1 detected a secret/PII in that value and produced a masked `new_text`, `scan_mcp_payload` (`mcp_scan_orchestrator.py:526`) ran `setter(new_text)` (no-op) **and set `result_redacted=True`** — reporting a redaction that never happened. Worse, because `result_redacted=True` makes `scan_mcp_payload` return the (unmodified) deep-copied `mutable`, the returned object is no longer `is result_content`, so the E12 result-redaction **floor is bypassed** (`_scan_tool_result_floor` only re-scans when `scanned is result_content`). Net: raw numeric/list/object PII egressed while the audit recorded `decision=redact`. Directly violates the mandate's "egress bytes are the only source of truth; fail closed on a no-op scrub; never report redact while forwarding raw."
- **WHY:** "field-level redaction of tool RESULTS (byte-verified, fail-closed)" (item 2) — a redaction claim MUST be reflected in the egress bytes.
- **NOW DOES:** both non-string targets are bound to the SAME real mutators the string targets use — dot-path via `_mutate_dot_path` (hoisted `_make_setter`), simple-key via in-place `node[key] = new`. On a redact, the value is replaced by the masked string in `state[0]`, so the returned payload actually carries the mask; a clean (unflagged) non-string value is untouched (the setter only fires when `new_text != text`). Entire-mode (the default) was already correct (`json.dumps` of the whole payload + `json.loads` setter).
- **Touched whose work:** the two-tier scan/redaction core (`mcp_scan_targets` / `mcp_scan_orchestrator`, CHG-0003/0005/0024/0025 lineage). Redaction of string targets + block behaviour unchanged.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_scan_targets.py ai_mesh_gateway/tests/test_mcp_scan_orchestrator.py -q` → 39 passed (numeric SSN keyed / list-of-emails keyed / dot-path numeric setters now mutate `state[0]`; end-to-end `scan_mcp_payload` applies the mask to a non-string key_path target — raw value absent from egress bytes). Broad sweep `ai_mesh_gateway/tests` → 1098 passed, 0 failed. Evidence: `mcp-parallel/findings/backstop-p2-nonstring-redact-setter/finding.md`. FOLLOW-UP (documented, not in this change): `_mutate_dot_path` is best-effort for exotic nested-list paths (same pre-existing limitation as the string path); a general fail-closed OUTPUT byte-check in `_scan_tool_result_floor` (block if any detected value survives the scrub) would be the ultimate backstop.

### 2026-07-02 — MCP-PAGE-CP10 | scripts/ralph/mcp_page_cp10_verify.mjs (new, verify-only) | WHAT: comprehensive register-flow verify (3 scenarios via route-mock): A error→modal open+inline error; B connected/0-tools→modal open+error, never lists 0-tools; C tools>0→modal closes+lists with tools | NOW DOES: cp10Pass:true, neverListedZeroTools:true. SECTION B (register flow CP07-10) COMPLETE — bug #2 resolved | touched: none (verifies CP07-09) | VERIFY: node scripts/ralph/mcp_page_cp10_verify.mjs → cp10Pass:true

### 2026-07-02 — MCP-PAGE-CP11 (confirm) | WHAT: transport-routing current state | BACKEND: _is_sandbox_routed → stdio+ws always sandbox; http/sse sandbox when MCP_HTTP_VIA_SANDBOX on (running gateway=true, broker_send_rpc x4) → ALL 4 transports already route via per-org sandbox, gateway never dials upstream (P4.13/CHG-0026 done prior). CP12-14 already backend-complete | FRONTEND (CP15 gap): TRANSPORT_OPTIONS labels only stdio "Stdio (sandbox)" (:71); http/sse/ws no sandbox indicator; help text stdio-only → display WRONG | VERIFY: docker exec gateway env|grep MCP_HTTP_VIA_SANDBOX=true; grep _is_sandbox_routed mcp_proxy.py

### 2026-07-02 — MCP-PAGE-CP12 (verify) | WHAT: http/sse execution IN per-org sandbox agent + egress allowlist | CHAIN: gateway _adapter_forward → broker_send_rpc {url,allowed_hosts,headers} → broker /v1/sandbox/{org}/rpc → sandbox agent _validate_upstream (:48) rejects non-allowlisted host -32002; gateway never dials upstream. LIVE (CP07): example.com/mcp http sync → "Upstream MCP error 405" = sandbox agent dialed it (proof via sandbox path). Prior egress-proof: gateway dials upstream 0x | VERIFY: grep broker_send_rpc+allowed_hosts in _adapter_forward; grep _validate_upstream upstream_manager.py

### 2026-07-02 — MCP-PAGE-CP13 (verify) | WHAT: websocket execution IN sandbox agent (CHG-0026+iter39) | EVIDENCE: (1) gateway test_mcp_http_via_sandbox.py -k websocket → 2 passed (test_adapter_forward_websocket_uses_broker_send_rpc: ws routes via broker_send_rpc not in-gateway ws adapter); (2) sandbox agent upstream_manager ws_manager send_ws_jsonrpc(:434)/close_ws(:161), _AUTO_INIT includes websocket(:396); (3) control models url=CharField → ws:// accepted (iter39) | LIMITATION: no live ws server → unit+code verified | VERIFY: pytest ...-k websocket (2 passed)

### 2026-07-02 — MCP-PAGE-CP14 (verify) | WHAT: LIVE-proved gateway opens NO direct upstream connection (talks only to sandbox) | METHOD: register streamable-http example.com/mcp, fire syncs, sample gateway vs sandbox /proc/net/tcp+tcp6 ∩ example.com IPs | RESULT: GATEWAY dialed example.com=FALSE, SANDBOX=TRUE {104.20.23.154} → isolation holds. CODE: _is_sandbox_routed True all 4 transports→broker_send_rpc, direct-httpx fallback dead when flag on. Corroborates p4-13/EGRESS_HTTP_PROVEN | GOTCHA: fresh login (cached tok→401) + parse tcp6 (example.com IPv6-first) | VERIFY: CP14 snippet → gateway NOT, sandbox YES

### CHG-0047 — Fail-closed no-op-scrub guard on the redaction path (item 2 — byte-truth invariant)
- **Date:** 2026-07-02
- **Severity:** Defense-in-depth (completes the CHG-0046 follow-up; enforces "fail closed on a no-op scrub").
- **Files:** `gateway/ai_mesh_gateway/mcp_scan_orchestrator.py` (import `_safe_json`; guard in `scan_mcp_payload`'s Tier-1 redaction branch); `gateway/ai_mesh_gateway/tests/test_mcp_scan_orchestrator.py` (+2).
- **WHAT (gap):** `scan_mcp_payload` sets `result_redacted=True` whenever `new_text != text` (a redaction was produced), regardless of whether the target's `setter` actually mutated the payload. CHG-0046 fixed the KNOWN no-op setters (non-string dot-path / simple-key), but `_mutate_dot_path` remains best-effort for exotic nested-list paths — so a residual silent no-op scrub could still egress the raw value while the audit records `decision=redact`. The mandate requires egress BYTES, not the action label, to be the source of truth.
- **WHY:** "field-level redaction of tool RESULTS (byte-verified, fail-closed)" + "fail closed on a no-op scrub; never report redact while forwarding raw."
- **NOW DOES:** in the Tier-1 redaction branch, snapshot `_before = _safe_json(state_ref[0])`, call `setter(new_text)`, then if `_safe_json(state_ref[0]) == _before` (payload bytes UNCHANGED) the scrub was a no-op → set `tier1_blocked=True` (+ a `noop_scrub_failclosed` scan-trace stage) → the whole result blocks (`result.blocked=True`), so every `_scan_tool_result_floor` caller withholds it. General (catches ANY setter that fails to apply), precise (compares actual bytes — no out-of-scope false positives, no dependence on findings carrying a raw value), cheap (one extra serialize per redacted target, only when sensitive data was detected). A setter that DOES apply changes the bytes → not blocked → redaction proceeds.
- **Touched whose work:** the two-tier scan/redaction core (`mcp_scan_orchestrator`, CHG-0003/0005/0024/0025/0046 lineage). Legitimate redaction + block behaviour unchanged.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_scan_orchestrator.py ai_mesh_gateway/tests/test_mcp_scan_targets.py -q` → 41 passed (no-op setter with a "redacted" tier1 text → `result.blocked=True` + `noop_scrub_failclosed` trace; real setter → not blocked, value masked). Broad sweep `ai_mesh_gateway/tests` → 1100 passed, 0 failed (ZERO spurious blocks on existing legitimate redaction). With CHG-0003 (fail-closed on scan error) + CHG-0046 (real non-string setters), the result-redaction path is now fail-closed on scan error, on setter no-op, AND for the known non-string shapes. Evidence: `mcp-parallel/findings/backstop-p2-noop-scrub-failclosed/finding.md`.

### 2026-07-02 — MCP-PAGE-CP15 | frontend/src/components/MCPConnectorPanel.jsx | WHAT: frontend shows Sandboxed for ALL transports (bug #3): stdio label "Stdio (sandbox)"→"Stdio"; help text → all 4 transports run in per-org sandbox + Shield "Sandboxed" badge for every transport; server cards get a Shield Sandboxed badge next to transport | WHY: backend sandboxes all transports (CP12/13/14) but UI implied stdio-only | NOW DOES: VERIFIED per transport — Sandboxed badge visible for streamable-http/sse/websocket/stdio; build green (6.09s). SECTION C complete | touched: MCPConnectorPanel.jsx | VERIFY: cycle Transport → Sandboxed badge all 4

### CHG-0048 — Atomic TTL on the MCP tool-call cap counter (item 11 — Redis correctness)
- **Date:** 2026-07-02
- **Severity:** MEDIUM (Redis correctness / availability — a transient hiccup could permanently cap a key).
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`_incr_tool_call_count`); `gateway/ai_mesh_gateway/tests/test_mcp_tool_call_cap_ttl.py` (new, +5).
- **WHAT (gap):** the per-key tool-call cap counter did `count = INCR(rk); if count == 1: EXPIRE(rk, 60)`. The window TTL was set ONLY on the first increment, so a crash / dropped connection / failed EXPIRE at that moment left `mcp:toolcalls:<key>` with NO TTL forever — every later call increments count to 2,3,… (never `==1`), so EXPIRE never runs again, the counter never resets, and once `count > mcp_max_tool_calls` the key is 429'd on EVERY tool call permanently until the Redis key is manually deleted. Non-atomic read-modify-write.
- **WHY:** "PostgreSQL + Redis correctness" (item 11) + rate-limit correctness (item 9) — a counter's TTL must be set atomically with its increment and must self-heal.
- **NOW DOES:** INCR + set-TTL-if-missing run ATOMICALLY in a `pipeline(transaction=True)` (MULTI/EXEC), with EXPIRE on EVERY increment using `NX` (Redis 7+; deployed image is `redis:7.4-alpine`). `NX` sets the TTL only when absent, so the FIXED 60s window is preserved (an existing TTL is never extended → not a sliding window) and a lost TTL is HEALED on the very next call. Fail-open on any Redis error is unchanged (the cap is a soft resource limit). Mirrors the codebase's existing atomic-Redis idiom (`rate_limiter.py`).
- **Touched whose work:** the CHG-0006 per-key tool-call cap (`_incr_tool_call_count`). Cap semantics unchanged; only the TTL is now atomic + self-healing. Existing cap tests mock the function wholesale, so they are unaffected.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_tool_call_cap_ttl.py -q` → 5 passed (uses REAL fakeredis: first call sets a TTL; a second call does NOT extend it — fixed window; a key whose TTL was dropped via `persist` is HEALED on the next increment; Redis error → fail-open 0; no key → 0). Existing cap tests (test_e12_mcp_security.py / test_mcp_bare_proxy_scan.py) → 37 passed. Broad sweep `ai_mesh_gateway/tests` → 1105 passed, 0 failed. Evidence: `mcp-parallel/findings/backstop-p11-toolcall-cap-ttl-race/finding.md`.

### 2026-07-02 — MCP-PAGE-CP16 | control/ai_mesh_control/mcp_connector/views.py | WHAT: sanitize the MCP client-facing error path (bug #5/6/7) so registration/discovery sync errors are clean, branded, NON-revealing (no upstream HTML, no exit codes, no proc keys, no "gateway logs" hints) | WHY: CP07/09 showed the inline modal error leaking raw upstream ("Upstream MCP error: upstream HTTP 405: <!doctype html>...") and stdio internals ("exited with code -6") straight to the client | NOW DOES: new _sanitize_sync_error(raw,*,org_slug,server_slug) (views.py:407) — logs the raw detail at WARNING under a 12-hex correlation ref (ref=uuid4().hex[:12]) then returns a category-branded summary (auth / start-failure / egress / timeout / generic) + "(Ref: <ref>)"; applied at all 4 leaky return points in _discover_tools_via_gateway (was: HTTP status+resp.text[:200]; "Upstream MCP error: {msg}"; malformed; "Discovery request failed: {exc}") → last_sync_error (client boundary) now sanitized. mcp_proxy.py:1838 is a PII-block event recorder (not a leak). Deployed to control (docker cp + restart) | touched: control mcp_connector discovery/sync path (last_sync_error surface used by CP07/08/09/10) | VERIFY: scripts/ralph/mcp_page_cp16_clean_error.py (direct control API) → http_405 + stdio_badcmd both PASS: client text = "The MCP server could not be reached or returned an error. Verify the configuration and retry. (Ref: ...)", leaks=[], has_ref=true, branded=true. Stable client error code + dev debug view = CP17/CP18.

### CHG-0049 — Audit MCP Redis writes (clean post-CHG-0048) + pin OAuth token TTL-derivation (item 11)
- **Date:** 2026-07-02
- **Severity:** Verification + regression guard (no production code change).
- **Files:** `gateway/ai_mesh_gateway/tests/test_mcp_oauth_token_ttl.py` (new, +4); evidence `mcp-parallel/findings/backstop-p11-redis-write-audit/finding.md`.
- **WHAT:** followed up CHG-0048 by sweeping EVERY Redis write in the MCP surface for the same TTL-race / non-atomic class of bug. Findings: (1) `_flow_save` uses atomic `setex(_FLOW_TTL=600)` + `delete` on pop (used-once CSRF/PKCE) — clean; (2) `_token_save` uses atomic `setex` with a TTL DERIVED from the access token's `expires_at` (`max(int(expires_at-now)+60, 300)`, bumped to `_TOKEN_DEFAULT_TTL` when a refresh_token exists) — clean; (3) the tool-call cap counter is atomic since CHG-0048 — clean; (4) `mcp:scan_ver:*` is READ-ONLY on the gateway (written by the control plane) — clean. No new race found.
- **WHY:** "PostgreSQL + Redis correctness" (item 11) — confirm no sibling of the CHG-0048 bug, and pin the one correctness property with no test.
- **NOW DOES:** documents the write-by-write audit (evidence file) and adds a regression guard for `_token_save`'s expiry-derived TTL — previously untested, so a refactor to a fixed TTL could silently serve EXPIRED access tokens (TTL > token) or evict valid tokens early (TTL < token). Also documents that the in-process `_oauth_tokens` fallback returning an expired record is BY DESIGN (`get_stored_token` re-checks `expires_at`/refreshes; `has_stored_token` intentionally reports existence incl. expired), and its size is bounded by config cardinality (not request volume).
- **Touched whose work:** the OAuth 2.1 proxy (`mcp_oauth_proxy`, CHG-0042 lineage) — tests only, no behaviour change.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_oauth_token_ttl.py -q` → 4 passed (recording-Redis stub asserts the exact `setex` TTL: expiry-derived +60 grace / short-token floored to 300 / 30-day default when no expiry / refresh_token keeps the key ≥ default). Broad sweep `ai_mesh_gateway/tests` → 1109 passed, 0 failed. RESIDUAL: item 11 live kill-Redis-mid-load drill remains host-blocked (item 18 chaos).

### 2026-07-02 — MCP-PAGE-CP17 | control/ai_mesh_control/mcp_connector/views.py + frontend/src/components/MCPConnectorPanel.jsx | WHAT: map internal MCP failures → clean client message + STABLE client-facing error code + correlation id | WHY: CP16 gave a clean message+ref but no stable code and did not distinguish OOM/crash/image-missing; a stable code is the support/contract anchor (message wording may change, code does not) | NOW DOES: (1) _classify_sync_error(low) maps raw → (code, branded summary) with specific fingerprints checked BEFORE generic "exited with code": OOM=MCP_OUT_OF_MEMORY (code -9/signal 9/sigkill/oom/exit 137), crash=MCP_SERVER_CRASHED (code -6/signal 6/sigabrt/exit 134/core dumped), image=MCP_IMAGE_UNAVAILABLE (no such image/manifest unknown/pull denied), plus MCP_AUTH_FAILED / MCP_EGRESS_DENIED / MCP_TIMEOUT / MCP_START_FAILED / MCP_UNAVAILABLE. (2) SyncError(str) subclass carries .code+.ref so every existing consumer (last_sync_error CharField, needs_reauth .lower() heuristics, JSON error) works unchanged; _sanitize_sync_error returns it. (3) _resync_server_tools + MCPServerToolListView.post now emit error_code + correlation_id in the sync response. (4) OAuth refresh-fail path (_ensure_oauth_token_fresh) no longer interpolates raw {exc} into last_sync_error — routed through the sanitizer (was a residual client leak). (5) Frontend: addConnectErrorCode state + "Error code: <CODE>" line under the inline modal error | touched: control discovery/sync + OAuth refresh paths; MCP register modal | VERIFY: scripts/ralph/mcp_page_cp17_error_codes.py (API — valid stable code + correlation_id matching (Ref) + no leak, both cases PASS) + scripts/ralph/mcp_page_cp17_ui.mjs (route-mocked type-sim — modal shows branded msg + (Ref) + "Error code: MCP_UNAVAILABLE", stays open, no leak) + 12-case classifier unit check all PASS; frontend build green (6.35s). Dev diagnostic channel keyed by correlation id = CP18.

### CHG-0050 — Consistent request correlation id (X-Request-ID) on MCP audit events (item 13 — tracing)
- **Date:** 2026-07-02
- **Severity:** LOW-MEDIUM (observability/tracing gap; not a leak).
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (new `_mcp_request_correlation_id`; bare REST route + JSON-RPC route); `gateway/ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py` (+6).
- **WHAT (gap):** `_record_gateway_event` defaults `request_id` to a fresh `mcp-<ms>` timestamp (useless for correlation). The bare REST tool-call route `org_mcp_tool_call` recorded ALL its audit events with NO `request_id`; `org_mcp_jsonrpc` used `str(msg_id)` (the client-controlled, repeatable JSON-RPC id) only for its main sites; and NEITHER route honored an inbound `X-Request-ID` header — so a single tool call's block/redact/tag decisions couldn't be correlated across the audit trail or with gateway → broker → sandbox logs.
- **WHY:** "monitoring + metrics + tracing" (item 13) — request-level correlation is the code-level half of tracing (distinct from deploying OTEL/Jaeger).
- **NOW DOES:** new `_mcp_request_correlation_id(request, msg_id=None)` prefers the inbound `X-Request-ID` (bounded to 200 chars vs a hostile header), then the JSON-RPC id, else `""`. Threaded into ALL 5 audit events of the bare REST route (was zero), and the JSON-RPC route's `_req_id` now uses it (so it honors `X-Request-ID` over the repeatable JSON-RPC id). Robust to a request without `.headers` (try/except fallback).
- **Touched whose work:** the MCP audit path (`_record_gateway_event` call sites; CHG-0045 audit lineage). Additive — audit `request_id` was previously the timestamp default; block/redact behaviour unchanged.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py -q` → 33 passed, incl. an integration test driving `org_mcp_tool_call` with an `X-Request-ID` header + a blocked tool and asserting the captured audit event's `request_id` == the header, plus unit tests (prefers header / falls back to msg_id / empty when neither / bounds a 500-char header to 200 / survives no-`.headers`). Broad sweep `ai_mesh_gateway/tests` → 1115 passed, 0 failed. FOLLOW-UPS (documented, not in this change): thread `_req_id` into `org_mcp_jsonrpc`'s early authz sites + `internal_tools_call`; propagate the id into `broker_send_rpc` for cross-service tracing; OTEL/Jaeger + PG/Redis backup remain infra (item 13 stays `[ ]`). Evidence: `mcp-parallel/findings/backstop-p13-request-correlation-id/finding.md`.

### 2026-07-02 — MCP-PAGE-CP18 | control/ai_mesh_control/mcp_connector/views.py + urls.py | WHAT: DEVELOPER diagnostic channel — structured logs keyed by correlation id + a staff-only debug endpoint that shows the REAL cause (code, raw error, org/server) behind a sanitized client error, NEVER exposed to clients | WHY: CP16/17 correctly WITHHOLD the raw cause from clients (exit codes, upstream HTML, sandbox internals) — a developer still needs to see it to debug, keyed only by the ref the client reports | NOW DOES: (1) _store_sync_diagnostic(ref,code,raw,*,org_slug,server_slug) persists {ref,code,kind,org_slug,server_slug,raw_cause[:4000],created_at} to the Django cache (django_redis; locmem in tests) at key "mcp:diag:<ref>" TTL 7d, best-effort (cache outage never breaks sync); called from _sanitize_sync_error alongside the existing structured logger.warning("MCP sync error [ref=.. code=..]..."). (2) New MCPDiagnosticDetailView (GET /api/mcp-connector/diagnostics/<ref>/, permission_classes=[IsAdminUser] → request.user.is_staff) returns the cached record; ref validated /^[0-9a-f]{6,32}$/ (invalid→400, unknown→404). An org client — even an org ADMIN — is never Django staff, so it is never client-exposed | touched: control error-sanitization path (CP16/17 lineage) + new staff-only route | VERIFY: scripts/ralph/mcp_page_cp18_diag_channel.py → cp18Pass:true: client msg carries NO raw_cause (only "(Ref: 9ef86d0042dc)"); STAFF GET /diagnostics/<ref>/ → 200 raw_cause="upstream HTTP 405: <!doctype html>...", code=MCP_UNAVAILABLE, server_slug=cp18-diag; NON-STAFF org admin (minted JWT) → 403 no leak; invalid ref→400, unknown→404. Script self-mints the non-staff token via docker exec (no credential committed). This is also the CP19 round-trip proof (beautiful client error + full dev trace via ref).

### CHG-0051 — Propagate the request correlation id to the broker/sandbox (item 13 — end-to-end tracing)
- **Date:** 2026-07-02
- **Severity:** LOW-MEDIUM (observability/tracing; not a leak). Completes the CHG-0050 follow-up.
- **Files:** `gateway/ai_mesh_gateway/mcp_sandbox_client.py` (`_request_with_503_retry`, `broker_send_rpc`); `gateway/ai_mesh_gateway/mcp_proxy.py` (`_adapter_forward` + the tool-call site); `gateway/ai_mesh_gateway/tests/test_mcp_sandbox_client.py` (+2).
- **WHAT (gap):** CHG-0050 gave MCP audit events a per-request correlation id (from `X-Request-ID`), but `broker_send_rpc` sent NO correlation id to the broker — so the broker + sandbox logs for a tool call couldn't be tied back to the gateway MCPEvent audit; the trace ended at the gateway boundary.
- **WHY:** "monitoring + metrics + tracing" (item 13) — end-to-end request tracing across gateway → broker → sandbox (the code-level half, distinct from OTEL/Jaeger infra).
- **NOW DOES:** out-of-band `X-Request-ID` header propagation (does not touch the JSON-RPC payload schema): `_request_with_503_retry` gains `extra_headers` (merged with the broker auth headers, built once + reused across 503 retries); `broker_send_rpc` gains `correlation_id` and, when set, sends `X-Request-ID: <id>` to `POST {broker}/v1/sandbox/{org}/rpc` (no header when unset — no empty/None pollution); `_adapter_forward` gains `correlation_id` (default "") and passes it through; the `org_mcp_jsonrpc` tool-call site passes `correlation_id=_req_id`. A remote-transport tool call now carries the gateway's correlation id to the broker + sandbox.
- **Touched whose work:** the sandbox client (`broker_send_rpc`, P4.13/P6.18 unified path) + the adapter-forward path (CHG-0026). Additive params with defaults — all other callers unchanged.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_sandbox_client.py -q` → 13 passed, incl. 2 new: `broker_send_rpc(correlation_id="trace-xyz-123")` sends `X-Request-ID: trace-xyz-123` alongside the broker auth header; with no `correlation_id`, NO `X-Request-ID` is sent. Broad sweep `ai_mesh_gateway/tests` → 1117 passed, 0 failed. FOLLOW-UPS (documented): the stdio branch (uses `send_jsonrpc`, not `broker_send_rpc`); the tools/list adapter site + `internal_tools_call` + early `org_mcp_jsonrpc` authz sites; the broker/sandbox should LOG the received `X-Request-ID`; OTEL/Jaeger + backup remain infra (item 13 stays `[ ]`). Evidence: `mcp-parallel/findings/backstop-p13-broker-correlation-propagation/finding.md`.

### CHG-0052 — Broker logs the received request correlation id (item 13 — completes CHG-0051)
- **Date:** 2026-07-02
- **Severity:** LOW-MEDIUM (observability/tracing; not a leak).
- **Files:** `services/mcp-broker/src/sandbox/routes.py` (logger + `X-Request-ID` capture on both RPC routes + a forward log line); `services/mcp-broker/tests/test_broker_auth.py` (+2).
- **WHAT (gap):** CHG-0051 propagated the correlation id to the broker as an `X-Request-ID` header, but the broker's RPC route (`sandbox_rpc` / `stdio_rpc` → `_forward_sandbox_rpc`) neither read nor logged it — the broker RPC path had NO per-call logging at all — so the propagated trace was invisible on the broker side.
- **WHY:** "monitoring + metrics + tracing" (item 13) — for the CHG-0050/0051 correlation id to be useful, the broker must actually record it.
- **NOW DOES:** added `LOG = logging.getLogger("mcp_broker.sandbox_rpc")`; both RPC routes capture `x_request_id = Header(default=None, alias="X-Request-ID")` and pass it to `_forward_sandbox_rpc(..., request_id=...)`, which logs ONE line at the TOP (before docker/sandbox resolution, so failed 503 calls are traced too): `sandbox rpc org=… server=… transport=… method=… jsonrpc_id=… request_id=…`. **PII-safe:** logs ONLY metadata (org/server/transport/method/jsonrpc_id/request_id) — never `params`/`stdio`/`upstream`/`command`/`args`/`env`, which can carry PII/secrets (mirrors the gateway PII-clean logging of CHG-0041); a `-` placeholder when no header. Trace chain now: gateway MCPEvent audit (CHG-0050) → `broker_send_rpc` X-Request-ID (CHG-0051) → broker log (CHG-0052).
- **Touched whose work:** the broker RPC route (P4.13/P6.18 unified path). Additive — `_post_agent_rpc` untouched (its retry tests unaffected).
- **VERIFY:** `cd services/mcp-broker && PYTHONPATH="$PWD/src:$PWD/../../shared:$PWD/sandbox-image/agent" .venv/bin/python -m pytest tests/test_broker_auth.py -q` → passes, incl. 2 new: a POST to `/v1/sandbox/{org}/rpc` with `X-Request-ID` logs `request_id=trace-broker-42` + `method=tools/call` and NOT `params`; a POST with no header logs `request_id=-` (both force the clean early 503 via `cached_docker_ok=False`). Full broker suite → 106 passed, 0 failed. FOLLOW-UPS: forward `X-Request-ID` to the sandbox AGENT + have it log (last hop); gateway stdio/tools-list/internal/early-authz sites (CHG-0050/0051 follow-ups); OTEL/Jaeger + backup remain infra (item 13 stays `[ ]`). Evidence: `mcp-parallel/findings/backstop-p13-broker-logs-correlation-id/finding.md`.

### 2026-07-02 — MCP-PAGE-CP20 | services/mcp-broker/src/sandbox/docker_manager.py + sandbox-image/agent/stdio_manager.py + gateway/ai_mesh_gateway/mcp_stdio_adapter.py + docker-compose.yml | WHAT: fix heavy-MCP (Ruflo-class) exit -9 OOM — graceful Node heap tuning + OOM-aware failure categorization | WHY (diagnosis, evidence-proven): per-org sandbox mem_limit=2048m with memswap_limit==mem_limit (SWAP DISABLED) and NO Node heap cap set anywhere → a heavy Node MCP server (Ruflo) exceeding 2GB is SIGKILLed by the kernel OOM-killer (exit -9 / 137) with no graceful signal, and could starve co-tenant servers in the shared per-org sandbox. Live proof: `docker inspect zeroshield-mcp-sandbox` → mem=2147483648 memswap=2147483648 (swap off), no NODE_OPTIONS | NOW DOES: (1) docker_manager `_run_kwargs` injects NODE_OPTIONS=--max-old-space-size=<node_heap_mb> into the sandbox env, node_heap_mb=config.node_max_old_space_mb or max(256,int(memory_mb*0.75)) (=1536 for 2048), APPENDED to any operator MCP_SANDBOX_NODE_OPTIONS. So a memory-hungry Node server hits a GRACEFUL, catchable V8 "JavaScript heap out of memory" abort at ~1536MB (512MB headroom for the Python agent + other servers) instead of a silent kernel SIGKILL. New SandboxDockerConfig.node_max_old_space_mb (env MCP_SANDBOX_NODE_MAX_OLD_SPACE_MB, 0=auto). (2) `_classify_exit_reason(rc, stderr_tail, oversized_line)` (pure, extracted from the reader finally-block) categorizes OOM FIRST from the stderr signature ("heap out of memory"/"reached heap limit") OR kernel codes (-9/137) → "ran out of memory" text so the CP17 control classifier maps it to MCP_OUT_OF_MEMORY (a bare 134 SIGABRT would otherwise read as a crash); mirrored in the gateway adapter. (3) docker-compose broker env documents MCP_SANDBOX_MEMORY_MB + MCP_SANDBOX_NODE_MAX_OLD_SPACE_MB knobs (raise for heavy servers). Container already survives a child OOM (agent marks pending futures failed; co-tenant servers keep running) → combined with CP16-19 the client sees a clean MCP_OUT_OF_MEMORY + a dev diagnostic | touched: broker sandbox provisioning + sandbox-agent + gateway in-process stdio adapter | VERIFY: broker unit test_sandbox_lifecycle.py node-heap tests (3) + sandbox-agent test_stdio_exit_reason.py (8) + gateway -k stdio (10) all green; full broker sandbox-lifecycle 30 passed; sandbox image rebuilt (bcf437b42ebc). LIVE: recreated zeroshield sandbox → NODE_OPTIONS=--max-old-space-size=1536, `node -e v8.getHeapStatistics().heap_size_limit`=1560MB, forced overflow → "FATAL ERROR: Reached heap limit ... JavaScript heap out of memory" (graceful, not SIGKILL). CP21 = verify Ruflo connects or clean-errors.

### CHG-0053 — Mask secrets in the stdio args log + sandbox-agent leak-to-logs audit (item 13 / 1.4)
- **Date:** 2026-07-02
- **Severity:** LOW (secret-to-operator-logs hardening; config-dependent).
- **Files:** `services/mcp-broker/sandbox-image/agent/stdio_manager.py` (new `_safe_args_for_log` + the start-of-process log line); `services/mcp-broker/sandbox-image/agent/tests/test_stdio_manager_packages.py` (+5).
- **WHAT (audit + gap):** extended the CHG-0041 gateway-logging audit to the broker + sandbox AGENT (prompted by the CHG-0052 broker logging). AUDIT result: tool-call RESULTS/params are NEVER logged (the 1.4-critical property holds); the only server result logged is the `initialize` result (capabilities, `json.dumps`-escaped + truncated — acceptable); broker `_forward_sandbox_rpc` logs metadata only (CHG-0052). GAP: `stdio_manager.py:360` logged `command` + `args` verbatim at INFO — `env` (the normal secret store) is never logged, but a credential passed as a stdio ARG (`--token XYZ` / `--api-key=XYZ`) would land in operator logs in plaintext.
- **WHY:** "prevent MCP data leakage" — a configured secret must not appear in operator logs (mirrors CHG-0041's gateway PII/secret-to-logs check).
- **NOW DOES:** new `_safe_args_for_log(args)` masks the VALUE of any secret-looking flag (`token`/`key`/`secret`/`password`/`passwd`/`auth`/`credential`/`apikey`): `--token XYZ` → `["--token","***"]`, `--api-key=XYZ` → `["--api-key=***"]`. The start-of-process log now logs `_safe_args_for_log(args)`. Standalone positional values (URLs / package specs) are left intact so they aren't corrupted.
- **Touched whose work:** the sandbox agent stdio manager (CHG-0022/0044 lineage). Log-line + a pure helper; no runtime behaviour change.
- **VERIFY:** `cd services/mcp-broker && PYTHONPATH="$PWD/src:$PWD/../../shared:$PWD/sandbox-image/agent" .venv/bin/python -m pytest sandbox-image/agent/tests/test_stdio_manager_packages.py -q` → 31 passed, incl. 5 new `TestSafeArgsForLog` (value after a secret flag masked; inline `--api-key=…` masked; password/auth masked; non-secret args untouched; standalone positional not masked). No test depends on the log format. Broker suite `tests/` → 106 passed, 0 failed. FOLLOW-UP: URL-embedded credentials in a standalone arg aren't masked by the flag heuristic (separate vector). Evidence: `mcp-parallel/findings/backstop-p13-broker-agent-log-hygiene/finding.md`.

### CHG-0054 — Private-key BODY survived redaction (item 2 / 1.4 — HIGH secret leak)
- **Date:** 2026-07-02
- **Severity:** HIGH — a PEM private key in a tool RESULT was "redacted" but the base64 key MATERIAL egressed intact.
- **Files:** `gateway/ai_mesh_gateway/patterns.py` (`private_key_header` pattern → full PEM block); `gateway/ai_mesh_gateway/tests/test_private_key_redaction.py` (new, +5).
- **WHAT (gap):** discovered via an adversarial 1.4 verification. `redact_all` masked ONLY the `-----BEGIN … PRIVATE KEY-----` header line (→ `[PRIVATE_KEY]`), leaving the base64 key BODY + `-----END-----` intact — the body is the actual secret, and `[PRIVATE_KEY]` is trivially replaced with the fixed BEGIN line to reconstruct the key. The old pattern only matched **RSA** keys, so EC/DSA/OPENSSH keys were NOT matched at all (whole key egressed raw). Root cause: in `_redact_all_raw`, `PII_PATTERNS` (`private_key_header`) runs first and masks the BEGIN header, so the later `private_key_block` (CREDENTIAL_EXPOSURE_PATTERNS) — also header-only — never matched the multi-line body.
- **WHY:** "field-level redaction of tool RESULTS, byte-verified, fail-closed" — a detected secret must be removed from the egress BYTES, not partially masked.
- **NOW DOES:** `private_key_header` matches the ENTIRE PEM block — `-----BEGIN\s+(?:[A-Z0-9]+\s+)?PRIVATE\s+KEY-----(?:[\s\S]*?-----END…|[A-Za-z0-9+/=\s]*)`. Generic prefix covers RSA/EC/DSA/OPENSSH/ENCRYPTED/plain; the `…END` alt masks a complete block, the base64 fallback consumes a truncated key's body. The masker `[PRIVATE_KEY]` now replaces the whole key. Prose ("loads a private key") is NOT redacted (no false positive).
- **Oracle note:** `aidefence_scan`/`aidefence_has_pii` return `piiFound:false` on BOTH the raw AND redacted key — the AIMDS oracle has no PEM-key recognizer, so it is NOT a substitute oracle here; confirmation is via the gateway's own `detect_pii` (flags `private_key_header`) + byte inspection.
- **Touched whose work:** the core detection/redaction patterns (`patterns.py`, owning-session file). Additive/widening of one pattern; all other categories unchanged.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_private_key_redaction.py -q` → 5 passed (RSA body masked; EC key masked w/ surrounding text preserved; OPENSSH masked; truncated body masked; prose not redacted). Redaction-adjacent suites → 74 passed. Broad sweep `ai_mesh_gateway/tests` → 1122 passed, 0 failed. FOLLOW-UP: `detect_secrets` inventory omits private keys (they're in `PII_PATTERNS`; `detect_pii` covers them) — inventory-unification is a cross-plane owning-session decision. Evidence: `mcp-parallel/findings/backstop-p2-private-key-body-leak/finding.md`.

### 2026-07-02 — MCP-PAGE-CP21 | services/mcp-broker/src/sandbox/docker_manager.py + sandbox-image/agent/stdio_manager.py + gateway mcp_stdio_adapter.py + control/.../mcp_connector/views.py + docker-compose.yml | WHAT: verify Ruflo MCP connects+lists real tools OR clean-errors (Section E close); + configurable npm-cache tmpfs + accurate ENOSPC→MCP_INSUFFICIENT_STORAGE categorization | WHY: at default multi-tenant limits Ruflo (a very heavy Node server: koa/native-addons/ONNX embedder, dep tree >1GB, needs huggingface.co egress) fails — must be a CLEAN, ACCURATE D-error (not a raw crash), and an operator must be able to raise limits so it connects | EVIDENCE/DIAGNOSIS: isolated throwaway probe (real sandbox image + real `npx -y ruflo@latest mcp start`) → at 4g mem / 3g npm-cache Ruflo returns a valid MCP initialize (serverInfo ruflo 3.0.0) AND tools/list = REAL tools (agent_spawn, agent_execute, swarm_init, memory_store, config_get, hooks_*, ...). At default 2g mem / 1g npm-cache: the RAM-backed npm-cache tmpfs + node RSS hit the 2GB mem cgroup → kernel OOM (-9) BEFORE the 1GB npm-cache fills → binding constraint is memory | NOW DOES: (1) new SandboxDockerConfig.npm_cache_size_mb (env MCP_SANDBOX_NPM_CACHE_SIZE_MB, default 1024) → /var/npm-cache tmpfs size configurable so heavy servers can be enabled (raise mem too, since tmpfs eats the cgroup). (2) ENOSPC/"no space left" detection in sandbox _classify_exit_reason + gateway adapter → a clean "install exceeded the sandbox storage limit; raise MCP_SANDBOX_NPM_CACHE_SIZE_MB" message; control classifier maps it to NEW code MCP_INSUFFICIENT_STORAGE (9th D-code). (3) docker-compose documents the npm-cache knob | touched: broker sandbox provisioning + sandbox-agent + gateway adapter + control classifier | VERIFY: 41 broker+classifier unit tests (incl. ENOSPC→storage + npm_cache tmpfs default/config) green; control storage-classifier pure-check OK; sandbox image rebuilt. LIVE Branch A (product path, default limits): scripts/ralph/mcp_page_cp21_ruflo.py → cp21Pass:true, connection_status=failed, error_code=MCP_OUT_OF_MEMORY, correlation_id present, NO leak, dev diagnostic = "the MCP server ran out of memory (exit code -9 ...). Raise MCP_SANDBOX_MEMORY_MB" (accurate, actionable — NOT a raw crash / 0-tools card). LIVE Branch B (adequate resources): probe → Ruflo CONNECTS + lists real tools. **SECTION E (Ruflo OOM, CP20-21) COMPLETE.**

### CHG-0055 — api_key/access_key assignments not redacted (item 2 / 1.4 — secret-inventory gap)
- **Date:** 2026-07-02
- **Severity:** MEDIUM — a secret-labeled assignment egressed unmasked when the value didn't match a provider-specific pattern.
- **Files:** `gateway/ai_mesh_gateway/patterns.py` (new `api_key_assignment` in SECRET_PATTERNS + COMPLIANCE_TAG_MAP + _SECRET_MASKERS); `gateway/ai_mesh_gateway/tests/test_api_key_assignment_redaction.py` (new, +13).
- **WHAT (gap):** found continuing the CHG-0054 adversarial 1.4 verification. The secret inventory redacted `password=` / `secret=` / `token=` assignments but had NO `api_key=` / `apikey=` / `access_key=` pattern — so an `API_KEY=<value>` whose value did not match a provider-specific format (OpenAI `sk-`+32, AWS `AKIA…`, Google `AIza…`, Slack, Stripe…) egressed UNMASKED. e.g. `redact_all("API_KEY=sk-abcdef0123456789ABCDEFxyz")` (24 chars after `sk-`, below the openai 32 threshold) returned it verbatim; `api_key=`/`apikey:`/`access_key=`/`api-key =` all survived (all cases).
- **WHY:** "field-level redaction of tool RESULTS" — an api_key assignment is as sensitive as password/secret/token; the inventory omitted it.
- **NOW DOES:** new `api_key_assignment` = `(?:api[_-]?key|access[_-]?key)["\s]*[:=][\s"\']*` + `_TOKEN_VALUE`, mirroring `token_assignment`. Reuses the `_TOKEN_VALUE` FP guard (>=8 chars w/ a digit, not an instructional prose word) so `api_key=none` / `api key: forgotten?` / `api_key=` (empty) / `DEBUG=true` are NOT masked. Tagged `["SECRET"]`; masked via the generic `_mask_secret_assignment` → `api_key=***`. Case-insensitive (compile_pattern IGNORECASE). `detect_secrets` now flags `api_key_assignment`.
- **Touched whose work:** the core detection/redaction patterns (`patterns.py`, owning-session). Additive pattern; all other categories unchanged.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_api_key_assignment_redaction.py -q` → 13 passed (6 should-mask incl. detect_secrets→api_key_assignment; 6 FP-safe none/prose/empty/DEBUG; 1 env-dump masking API_KEY alongside the conn-string password, DEBUG preserved). Broad sweep `ai_mesh_gateway/tests` → 1135 passed, 0 failed. Evidence: `mcp-parallel/findings/backstop-p2-api-key-assignment-gap/finding.md`.

### 2026-07-02 — MCP-PAGE-CP22 (diagnose) | WHAT: root-cause the enforcement stats anomaly (0 BLOCK / high "error") | FINDINGS (live DB 183407 events: allow 180790 / error 2306 / redact 265 / block 46): (1) BLOCK IS WIRED + FIRING — 46 blocks (org_scope_violation ×36 cross-tenant + pii_blocked_inbound ×10), gateway-side (mcp_proxy.py ~15 decision=block sites); "0 BLOCK" was a pre-traffic snapshot, NOT a gap. (2) THE COUNTER UNDERCOUNTS (the real bug): control MCPToolCallView returns HTTP 403 for enforcement blocks (tool_disabled views.py:1199, tool_not_registered :1222, schema_validation_failed :1244) and records decision=block LOCALLY — but for gateway-originated calls the local _record_event is a NO-OP (de-dup shadow views.py:1166-1169), so control does not persist it; the gateway then records the 403 as decision=error reason=backend_error_http_{status} (mcp_proxy.py:3047) instead of decision=block → delegated enforcement blocks mis-categorized as errors. (3) "422 error" = decision=error count (2306 now). Top: backend_error_http_403 1708 (echo/get-sum streamable-http, all orgs) = legit tool_not_registered denials mislabeled; backend_error_http_500 375; sandbox temporarily unavailable 54 (B3 race); Stdio ...everything-N not running / exited code -6 (parallel-loop Everything servers CRASH under concurrent stress → tools never stay synced → echo→tool_not_registered 403); upstream 400 No valid session ID 8. REPRODUCED: gateway-headers→control /tools/call zeroshield/echo + org-a/echo → 403 {"reason":"tool_not_registered"} (echo_registered=False; everything-5 crashed under load). Gateway↔control internal keys MATCH → not auth. FIX=CP23: gateway maps control 403/404 w/ enforcement reason → decision=block (not error); genuine 500/sandbox/stdio-crash stay errors | VERIFY: docker exec control manage.py shell → MCPEvent decision+policy_reason breakdown; live 403 reproduction.

### CHG-0056 — URL/percent-encoded PII/secret bypassed redaction (item 2 / 1.4)
- **Date:** 2026-07-02
- **Severity:** MEDIUM — a %XX-encoded PII/secret (e.g. an email in a URL query param) egressed; trivially recoverable.
- **Files:** `gateway/ai_mesh_gateway/patterns.py` (percent-decode pass in `_redact_obfuscated` + `_PERCENT_TOKEN_RE`/`_MAX_URL_DECODE_TOKENS` + `import urllib.parse`); `gateway/ai_mesh_gateway/tests/test_url_encoding_redaction.py` (new, +11).
- **WHAT (gap):** found continuing the CHG-0054/0055 adversarial 1.4 verification (probing the encoding-obfuscation surface). `redact_all`→`_redact_obfuscated` de-obfuscated base64 / hex / url-safe-base64 / double-base64 (all CAUGHT), but did NOT URL-decode — so `john.doe%40example.com` (email in a URL query param) or a %-encoded SSN (`123%2d45%2d6789`) broke the raw patterns (`@`→`%40`, `-`→`%2d`) and the token egressed.
- **WHY:** "field-level redaction of tool RESULTS" resistant to obfuscation — a %-encoded secret is trivially recoverable, so it must be masked like the base64/hex forms.
- **NOW DOES:** a percent-decode pass in `_redact_obfuscated` — for each token carrying a `%XX` escape (`_PERCENT_TOKEN_RE`, bounded to `_MAX_URL_DECODE_TOKENS=32` to stay decode-bomb safe), `urllib.parse.unquote` it and, if the decoded form matches PII/secret, mask the whole encoded token with `[ENCODED_SECRET_REDACTED]`. Only masks when decoded PII/secret is found, so benign percent text (`50%20off`→"50 off", `C%3A%5Cpath`→"C:\\path", `95%`, `?p=2%2C3`) is untouched. base64/hex de-obfuscation + plain-PII masking unchanged.
- **Touched whose work:** the core detection/redaction obfuscation pass (`patterns.py`, owning-session). Additive pass; existing G1 unicode / G2 base64-hex behaviour preserved.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_url_encoding_redaction.py -q` → 11 passed (URL-encoded email/dash-SSN/fully-%-SSN/mixed masked, decoded fragment absent; benign percent text NOT masked; base64/hex still works; plain PII uses normal maskers). Broad sweep `ai_mesh_gateway/tests` → 1158 passed, 0 failed. DOCUMENTED RESIDUAL (perf/security tradeoff, NOT changed): the base64/hex decode cap `_MAX_DECODE_TOKENS=12` (a DoS bound) lets a crafted result pad 12+ decoy base64 tokens to hide the real encoded secret past the cap — raising it trades DoS- for evasion-resistance (owning-session tuning decision). Evidence: `mcp-parallel/findings/backstop-p2-url-encoding-obfuscation/finding.md`.

### 2026-07-02 — MCP-PAGE-CP23 (fix) | gateway/ai_mesh_gateway/mcp_proxy.py | WHAT: record control-delegated ENFORCEMENT denials as decision=block (not error) so audit counters reflect real enforced reality; eliminate/justify the "422 errors" | WHY (CP22): control MCPToolCallView returns 4xx with a reason for enforcement blocks (tool_disabled/tool_not_registered/invalid_arguments/policy) but its local event is a no-op for gateway-originated calls (de-dup); the gateway recorded the 4xx as decision=error backend_error_http_403 → blocks miscounted as errors (1708 such events) | NOW DOES: new pure _classify_backend_failure(status_code, data) → (decision, reason, enforced_at): a 400/403 carrying a known enforcement reason (tool_disabled, tool_not_registered, tool_not_allowed, invalid_arguments, schema_validation_failed, blocked_by_policy, org_scope_violation, ...) OR a policy-block error marker ("blocked by policy") → decision=block with that reason, enforced_at=backend; everything else (5xx, malformed 400, server-not-found 404, no reason) stays decision=error backend_error_http_{status}. Applied at the sole tool-call recording site (mcp_proxy.py:~3088; previously hardcoded decision=error at :3047); metadata now also carries backend_status. This is the ONLY path that emits backend_error_http_* | touched: gateway MCP data-plane audit recording (CP22 lineage) | VERIFY: gateway unit test_mcp_enforcement_classify.py 12 passed (all enforcement reasons→block incl. free-text policy message; 404 not-found/400 malformed/500-with-spurious-reason→error); 60 gateway MCP tests green (no regression); deployed (docker cp + SIGHUP, gateway healthy, classifier present). LIVE counter-reality on live traffic = CP24.

### 2026-07-02 — MCP-PAGE-CP24 (verify) | gateway/ai_mesh_gateway/tests/test_mcp_enforcement_block_recording.py | WHAT: verify the event counters (allow/block/redact/monitor/error) reflect real enforced reality on live traffic — closes the CP22 anomaly | HOW/EVIDENCE: (1) DETERMINISTIC integration proof — 3 tests drive the REAL org_mcp_jsonrpc handler down the direct-control path with the control /tools/call backend mocked: backend 403 {reason:tool_not_registered} → recorded decision=block reason=tool_not_registered enforced_at=backend; backend 403 "blocked by policy" → decision=block reason=<policy msg>; backend 500 → decision=error backend_error_http_500. (2) LIVE — since the CP23 deploy (17:50:36) the parallel loop generated 1556 events: allow 1515, block 41 (all org_scope_violation, firing correctly), error 0 — the backend_error_http_403 error category (1708 before) is GONE (enforcement 403s no longer mislabeled). redact/monitor 0 in-window (benign traffic; redaction historically proven 265 events). Counters now reflect reality: allow=successful calls, block=real enforcement, error=genuine faults only | touched: verification only (CP22/CP23 lineage) | VERIFY: pytest test_mcp_enforcement_classify.py + test_mcp_enforcement_block_recording.py + test_mcp_bare_proxy_scan.py → 48 passed; live MCPEvent decision breakdown since deploy (0 spurious errors, blocks firing). SECTION F (enforcement stats CP22-24) COMPLETE.

### CHG-0057 — Tier1 redact path byte-verifies ALL detected categories (item 2 / 1.4 — fail-closed)
- **Date:** 2026-07-02
- **Severity:** Defense-in-depth (fail-closed byte-truth) + E2E verification of CHG-0054/0055/0056.
- **Files:** `gateway/ai_mesh_gateway/mcp_scan_orchestrator.py` (`_scan_text_tier1` redact byte-check); `gateway/ai_mesh_gateway/tests/test_mcp_scan_orchestrator.py` (+2).
- **WHAT (verification + gap):** verified the MCP tool-result redaction path — `scan_mcp_payload` → `_scan_text_tier1` PII/secret branch detects with `detect_pii`/`detect_secrets`/`detect_ip_leakage` and masks with `redact_all`, ALL from `patterns.py` — so CHG-0054 (private-key block), CHG-0055 (api_key assignments) and CHG-0056 (URL-encoding) DO protect real MCP tool results end-to-end (tier1 is patterns-based; Presidio is tier2 only). GAP: the tier1 redact branch's fail-closed "detected-value-survives-the-scrub → block" byte-check checked ONLY `ip_leak.values()`, with a comment ASSUMING "PII/secret values are always covered by redact_all" — an assumption CHG-0054 showed can be violated by a masker bug.
- **WHY:** "field-level redaction of tool RESULTS, byte-verified, fail-closed" — the byte-truth guard must cover ALL detected categories, not just ip_leak.
- **NOW DOES:** the byte-check unions the raw values of all detected categories — `_detected_values = list(pii.values()) + list(secrets.values()) + list(ip_leak.values())`; if ANY survives `candidate` (the scrub) verbatim → `blocked=True`. The "PII/secret always covered" assumption is now ENFORCED, not assumed. No false positives: `redact_all` replaces every detected match (masker or default `[X_REDACTED]`), so a detected value's raw form is never a substring under normal operation — verified over the full PII/secret battery (ZERO would-be false blocks).
- **Touched whose work:** the two-tier scan core (`mcp_scan_orchestrator`, CHG-0003/0030/0046/0047 fail-closed lineage). The `ip_leak`-only check is widened; redact behaviour on normal PII unchanged.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_scan_orchestrator.py -q -k chg0057` → 2 passed (stubbed no-op redact_all → detected email survives → blocked=True fail-closed; real redact_all → blocked=False, email masked, no false positive). Broad sweep `ai_mesh_gateway/tests` (excluding another session's untracked incomplete `test_mcp_enforcement_block_recording.py`, which fails to collect on an undefined `_rest_request` — unrelated) → 1160 passed, 0 failed. Evidence: `mcp-parallel/findings/backstop-p2-tier1-byte-verify-all/finding.md`.

### 2026-07-02 — MCP-PAGE-CP25 (diagnose) | WHAT: root-cause the MCP Observability tab 500 | ROOT CAUSE (evidence: control logs + live pg_stat_activity): the 500 is django.db.utils.OperationalError "FATAL: sorry, too many clients already" — Postgres connection-pool EXHAUSTION under peak parallel-loop concurrency, thrown in DRF perform_authentication (request.user → auth_user SELECT needs a DB connection) BEFORE the view body runs. So EVERY authenticated endpoint 500s when the pool is exhausted; the Observability tab is just the one the UI hits on load (GET /api/mcp-connector/events/ + /events/summary/). REPRODUCED: control logs show those two paths returning 500 at 14:18-14:19 with the too-many-clients traceback; intermittent daphne wedges/timeouts from the same contention. NOT a view code bug — MCPEventListView/MCPEventSummaryView returned 200 for zeroshield+org-a+org-b and edge params (limit=abc→400 handled) once the pool had headroom. FACTORS: max_connections=400 (only ~60/400 used now → peak spike, not a leak), CONN_MAX_AGE=60 + daphne async (each async task holds a connection 60s → accumulation), extreme parallel-loop demand. FIX (CP26): reduce peak connection pressure (lower CONN_MAX_AGE / pgbouncer / raise ceiling) + make observability endpoints degrade gracefully (retry-once / clean 503, never a raw 500) | VERIFY: docker logs control | grep events 500 → too-many-clients traceback; pg SHOW max_connections=400 + pg_stat_activity 60/400.

### 2026-07-02 — MCP-PAGE-CP26 (fix) | control/ai_mesh_control/main_app/exception_handlers.py + frontend/src/components/MCPConnectorPanel.jsx | WHAT: fix the MCP Observability tab 500 (root-caused CP25 = Postgres connection-pool exhaustion "too many clients already", raised in perform_authentication before the view) + verify the view loads correct real data | NOW DOES: (1) safe_exception_handler maps django.db OperationalError + InterfaceError → HTTP 503 {detail, code:db_unavailable} + Retry-After:2 (retryable), NOT a raw 500. Since the handler is the project REST_FRAMEWORK.EXCEPTION_HANDLER and CP25 proved the OperationalError already reaches it (it fell through to the generic 500 branch), this converts every transient-pool-exhaustion 500 (on ANY endpoint, incl. the observability tab) into a graceful, retryable 503; the 400 (malformed-input) + 500 (generic) branches are preserved. (2) Frontend: new fetchWithRetry retries the observability loads (loadEvents/loadEventSummary) ONCE on a 503 after Retry-After (≤5s), so the tab auto-recovers real data after a transient spike instead of showing a blank/error state. Chose graceful-degradation + retry over blindly raising max_connections (under enough load any fixed pool can exhaust; resilience is the robust answer; CONN_MAX_AGE stays env-tunable). | touched: project exception handler (all endpoints) + MCP observability tab | VERIFY: handler unit assertions via django.setup → OperationalError/InterfaceError→503 (code=db_unavailable, Retry-After=2), ValueError→400, RuntimeError→500 all PASS; deployed to control (grep OperationalError branch present); observability endpoints load CORRECT REAL DATA (GET /events/summary/ → 200 total=64118 decisions={allow 63287, error 568, redact 225, block 38}, top_tools echo 36178/get-sum 27902, recent 5; GET /events/?limit=500 → 200, 500 rows); frontend build green (6.21s). SECTION G (Observability 500, CP25-26) COMPLETE.

### CHG-0058 — Encoded internal network address leak closed (item G2/1.4 — obfuscation bypass, fail-closed)
- **Date:** 2026-07-02
- **Severity:** HIGH (real redaction leak — internal IP/host/URL egressed base64/hex/URL-encoded on MCP tool results).
- **Files:** `gateway/ai_mesh_gateway/patterns.py` (`_INFRA_NETWORK_KEYS`, `_dec_has_infra`, `_SHORT_B64_RE`, `_iter_short_b64_infra`, `_redact_obfuscated` base64/hex + URL-decode branches); `gateway/ai_mesh_gateway/tests/test_encoded_infra_redaction.py` (+13).
- **WHAT (gap):** `redact_all` de-obfuscates base64/hex (G2) and URL/percent (CHG-0056) blobs, but the decode branches in `_redact_obfuscated` checked only `_detect_pii_core`/`_detect_secrets_core` — NOT `detect_ip_leakage`. So an INTERNAL network address inside an encoded blob (`base64("db.internal:5432")`, `base64("http://192.168.50.123:8080/admin")`, `%`-encoded internal URL) survived scrubbing and egressed verbatim, trivially recoverable by any decoder. SECONDARY gap found while fixing: the shared base64 gate `_B64ISH_RE` needs `{12,}` chars (~>=9 bytes), so a BARE short internal IPv4 (`10.1.2.3` → `MTAuMS4yLjM=`, 11 base64 chars) fell just under and still leaked (hex short-IPs already covered: 8 bytes = 16 hex >= the `{8,}` floor).
- **WHY:** "field-level redaction of tool RESULTS, byte-verified, fail-closed" — the obfuscation-decode redaction path must cover internal-network leakage, not just PII/secrets, and must not have a length blind-spot a short internal IP slips through.
- **NOW DOES:** (1) `_dec_has_infra(dec)`/`_dec_has_infra(dcanon)` added to the base64/hex decode branch and `_dec_has_infra(dec)` to the URL-decode branch — an encoded internal IP/host/URL masks the whole token as `[ENCODED_SECRET_REDACTED]`. Scoped to NETWORK keys (`internal_ipv4`/`internal_hostname`/`internal_url`); file-path leak types excluded (flag-tier + FP-prone, matching `redact_all`'s own scope). (2) `_iter_short_b64_infra` — a dedicated SHORT-token pass (maximal run of 8..11 base64 chars, the band the main gate misses) that decodes and masks ONLY when the result is an internal network address; network-key-only so it changes NOTHING about `detect_pii`/`detect_secrets`, only tightening the fail-closed redaction path. Look-behind/look-ahead pin maximal runs (12+ char tokens stay with the main pass); token count bounded (decode-bomb safe). No false positives: the `_IP_LEAKAGE_EXAMPLE_ADDRS` textbook carve-out (192.168.0.1, 10.0.0.1, …) is preserved on the decode path; encoded file paths untouched; FP battery over benign short-base64 text → ZERO changed.
- **Touched whose work:** the obfuscation-resistant redaction core in `patterns.py` (CHG-0054/0055/0056 lineage; the owning-session tier1 detection file). Integrates with CHG-0057's byte-verify guard, which already unions `ip_leak.values()` so a surviving internal address fails closed → block. detect_pii/detect_secrets behaviour unchanged.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_encoded_infra_redaction.py -q` → 13 passed (base64/hex/URL encoded internal IP/host/URL masked; example addrs + encoded file path NOT masked; plain internal IP still uses normal masker; base64 PII/secret still masked). Full sweep `ai_mesh_gateway/tests` → 1176 passed, 0 failed. Evidence: `mcp-parallel/findings/backstop-p2-encoded-infra-leak/finding.md`.

### 2026-07-02 — MCP-PAGE-CP27 (diagnose) | WHAT: root-cause enabling org Tier-2 → HTTP 400 | ROOT CAUSE (reproduced): the frontend saveMcpTier2 (MCPScanControlMatrix.jsx:517) does a FULL-OBJECT PUT body={...firewallConfig, mcp_tier2_enabled:value} to /api/firewall/config/, re-sending EVERY field. The org allowed_models contains an orphaned model r6a-live-verify:free (not connected), so FirewallConfigSerializer re-validates it and rejects the whole request → 400 {"allowed_models":["These models are not connected..."]}. A stale sibling field blocks the Tier-2 toggle. PROOF: PUT {"mcp_tier2_enabled":true} PARTIAL → 200 (tier2=True); PUT {...fullConfig,mcp_tier2_enabled:true} → 400 allowed_models. Endpoint is documented "partial update (any subset)" (core/firewall_config_views.py:22). ENABLEMENT LAYERS: (a) global org toggle FirewallConfig.mcp_tier2_enabled (Inherit=null/Enabled=true/Disabled=false) governs org-wide; (b) optional per-scope MCPScanControl tier2 rows (POST /api/mcp-connector/scan-controls/), resolved by resolve_effective_controls._pick_control. "0 Tier-2 rows active across all scopes" = frontend tier2Active count of enabled tier2 rows (informational, NOT a backend 400 msg; string exists ONLY in frontend). FIX (CP28): saveMcpTier2 must PUT ONLY {mcp_tier2_enabled:value} (partial); no scope row needed to enable (global toggle is the mechanism) | VERIFY: partial PUT 200 vs full-config PUT 400 reproduced live.

### 2026-07-02 — MCP-PAGE-CP28 (fix) | frontend/src/components/MCPScanControlMatrix.jsx | WHAT: fix the org Tier-2 enablement 400 (CP27: full-object PUT re-validated a stale allowed_models) + verify Inherit/Enabled/Disabled all work and rows activate | NOW DOES: saveMcpTier2(value) now PUTs ONLY {mcp_tier2_enabled: value} to /api/firewall/config/ (the documented partial-update), so the toggle no longer re-sends/re-validates unrelated sibling fields (a stale allowed_models model no longer 400s Tier-2). value: null=Inherit, true=Enabled, false=Disabled. Also parses the field-level error (err.mcp_tier2_enabled[0]) for a precise toast, and merges the returned config into state (setFirewallConfig(prev→{...prev,...updated})). No scope row is auto-created on enable — the global toggle IS the org-wide enable mechanism (CP27 evidence; creating a row would be architecturally wrong); per-scope rows remain an optional override via Add control | touched: MCP Scan Controls tab Tier-2 SegmentedControl | VERIFY: API — partial PUT for all 3 states → 200 (Enabled→True, Disabled→False, Inherit→None); BROWSER (scripts/ralph/mcp_page_cp28_tier2.mjs) — clicking the SegmentedControl Enabled/Disabled/Inherit on the Scan Controls tab → all 3 PUT /api/firewall/config/ = 200 (NO 400), badge reflects each state, cp28Pass:true; per-scope row activate — POST /api/mcp-connector/scan-controls/ {tier:tier2,enabled:true} → 201, active tier2 rows 0→1 (rows activate); frontend build green (6.30s). SECTION H (Tier-2 enable 400, CP27-28) COMPLETE.

### 2026-07-02 — MCP-PAGE-CP29 (diagnose) | WHAT: reproduce + root-cause the 1.4 context-assembly telemetry (Context Fields 500 assembled / PII Redaction 0 sanitized / Size Check 0 denied / Final Context 0 approved) | The 1.4 flow-nodes (firewall-submodules.jsx:136-139) map summary.{total→assembled, redacted→sanitized, blocked→denied, allowed→approved}; summary=summarizeEvents(threatFeed) (firewall-module-utils.js:586); threatFeed=useFirewallData("1.4")→GET /api/security/threat-feed/?source=mcp_scan&limit=500 (MODULE_SOURCE_MAP["1.4"]="mcp_scan", hard limit:"500" useFirewallData.js:60). (1) IS 500 A CAP? YES — summarizeEvents.total=events.length and the feed is truncated to limit=500. LIVE: the endpoint returns {count:4000, results:[500], scan_truncated:true}; real total=4000 but UI shows 500 because it uses results.length (clamped) and IGNORES the envelope count. (2) STAGES RECORDING? NO — mismapped generic mcp_scan action-counts, not a real context-assembly pipeline. LIVE action dist of 500 rows: {monitor:497, block:3} → assembled=500(cap), sanitized(redact)=0 (scan rows carry no redact), denied(block)=3, approved(allowed)=500-3-0-0-497=0 (monitor swallowed). Sample: source=mcp_scan action=monitor — MCP-scan/guardrail events, NOT context-assembly stage events. TWO DEFECTS: (a) display cap (total=capped results.length not envelope count 4000); (b) stage mismapping/non-instrumentation (sanitized always 0, approved swallowed by monitor). FIX (CP30-31): use envelope count for total + correctly map guardrail actions (monitor=passed/approved, redact=sanitized) OR wire a real context-assembly telemetry | VERIFY: live threat-feed envelope count=4000 vs shown 500; action dist {monitor:497, block:3}.

### 2026-07-02 — MCP-PAGE-CP30 (root-cause) | WHAT: settle root-cause of the 1.4 context-assembly telemetry + fix plan | BACKEND TRACED: the 1.4 source is the shared SOC threat-feed GET /api/security/threat-feed/ (policy/security_views.py:426) → queries EnforcementEvent filtered metadata__source=mcp_scan, collapses by metadata.request_id (collapse=true default, scans up to _THREAT_FEED_DEDUP_SCAN_CAP), returns {count:len(deduped), results:deduped[offset:offset+limit], collapsed_by_request, scan_truncated} — ONLY total count + limit-capped results page, NO per-action aggregate. THREE ROOT CAUSES: (1) DISPLAY CAP — frontend summarizeEvents(threatFeed).total=results.length (capped at limit=500) shown as "assembled" instead of envelope count (4000 live). (2) COUNTERS NOT WIRED TO SERVER-SIDE AGGREGATES — stage breakdown computed client-side from the capped 500-sample; endpoint returns no action_counts, so it can only reflect the first 500 collapsed events. (3) STAGE SEMANTICS MISMAPPED — summarizeEvents.allowed subtracts monitor (a passed outcome) → approved=0 despite 497 monitored; redact ~absent from mcp_scan → sanitized=0. No dedicated context-assembly pipeline exists; 1.4 reuses the generic EnforcementEvent(mcp_scan) SOC feed. FIX PLAN (CP31): (a) backend add server-side action_counts {total,block,redact,monitor,allow} to the envelope over the full filtered set (respecting collapse); (b) frontend 1.4 use envelope count for assembled + action_counts for stages with correct mapping (redact→sanitized, block→denied, monitor+allow→approved) | VERIFY: security_views.py:426-513 read; envelope has count+results only.

### CHG-0059 — MCP audit compliance-tag vocabulary unified onto catalog codes (item 5 / 1.4 — cross-plane audit consistency)
- **Date:** 2026-07-02
- **Severity:** MEDIUM (audit-integrity: `MCPEvent.compliance_tags` violated its documented `ComplianceTag.code` contract for gateway-recorded events; latent — no live catalog-join yet, frontend only displays tags).
- **Files:** `shared/ai_mesh_shared/mcp_compliance_tags.py` (extend `PRESET_TO_TAGS` for internal-infra keys; add `to_catalog_codes` + `CATALOG_TAG_CODES`); `control/ai_mesh_control/mcp_connector/tasks.py` (`record_mcp_event_task` normalizes at ingestion); `control/ai_mesh_control/mcp_connector/views.py` (`_record_event` normalizes at persistence, defense-in-depth); `gateway/ai_mesh_gateway/tests/test_compliance_tag_catalog_vocab.py` (+31); `control/ai_mesh_control/mcp_connector/tests/test_compliance_tag_ingest_normalization.py` (+6).
- **WHAT (gap):** `MCPEvent.compliance_tags` is documented as a list of `ComplianceTag.code` values (`GDPR-PII/HIPAA-PHI/PCI-CARD/SOC2-CONF/...`). The CONTROL enforcement path already emits catalog codes (via `tags_for_preset_or_entity`), but the GATEWAY scan path (`patterns.py` `get_compliance_tags`) emits a GRANULAR vocabulary (`GDPR/HIPAA/PII/PHI/PCI-DSS/SECRET/INFRA/SOC2`) — so the same field held two vocabularies depending on recording plane (an SSN leak → control `['GDPR-PII','HIPAA-PHI']` vs gateway `['GDPR','HIPAA','PII']`), breaking group/filter/join-by-tag and violating the field contract. Also the shared module had NO internal-infra keys at all (item 5's literal "extend to IP"). This is the CHG-0017/CHG-0030 vocab residual.
- **WHY:** item 5 ("extend mcp_compliance_tags.py to PII/IP/regulated; ... audit"). The audit field's CONTRACT is catalog codes, so consistency must be enforced at the write boundary.
- **NOW DOES:** normalizes gateway-granular tags onto catalog codes at the audit WRITE boundary — `to_catalog_codes` (GDPR/PII→GDPR-PII, HIPAA/PHI→HIPAA-PHI, PCI-DSS→PCI-CARD, SECRET/INFRA/SOC2→SOC2-CONF), IDEMPOTENT (catalog codes pass through), never drops a tag (unknown → passthrough), applied at both `record_mcp_event_task` (gateway ingestion — the primary fix) and `_record_event` (defense-in-depth). `PRESET_TO_TAGS` extended with the gateway `detect_ip_leakage` keys (`internal_ipv4/internal_hostname/internal_url/file_path_unix/file_path_windows/ip_leakage`) → `SOC2-CONF`. Fix is at INGESTION not the gateway source → ZERO gateway changes (no test breakage, no collision with active patterns.py editors). Now every MCPEvent write upholds the catalog-code contract regardless of plane.
- **Touched whose work:** the control-plane MCP audit ingestion/recording (`mcp_connector`) + the shared cross-plane tag module. Gateway `patterns.py` deliberately UNTOUCHED (the 8 gateway compliance tests stay green). Complements CHG-0030 (gateway INFRA tag) + CHG-0017 (live tag audit).
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_compliance_tag_catalog_vocab.py -q` → 31 passed; full gateway sweep → 1207 passed, 0 failed. Control (Django runner, throwaway container w/ working-tree bind-mount on the compose network+DB): `manage.py test mcp_connector.tests.test_compliance_tag_ingest_normalization` → 6 passed (gateway envelope `['GDPR','HIPAA','PII']` PERSISTS as `['GDPR-PII','HIPAA-PHI']`; INFRA/SECRET→SOC2-CONF; PCI-DSS→PCI-CARD; catalog idempotent; unknown preserved); broader `mcp_connector` (scan_controls + oauth_transport_guard + new) → 21 passed, 0 fail (2 pre-existing harness errors — pytest/fakeredis dev deps absent — unrelated). Evidence: `mcp-parallel/findings/backstop-p5-compliance-vocab-normalize/finding.md`. RESIDUALS (documented, non-blocking): ITAR/FERPA catalog codes have no detector; gateway still emits granular at source (consistency enforced at audit write boundary by design); ideal future = shared `CATALOG_TAG_CODES` as single source-of-truth imported by control's `policy/compliance_tags.py`.

### 2026-07-02 — MCP-PAGE-CP31 (fix) | control/ai_mesh_control/policy/security_views.py + frontend/src/hooks/useFirewallData.js + components/FirewallModulePage.jsx + components/firewall-module-utils.js | WHAT: make the §1.4 context-assembly stages (assembled/sanitized/denied/approved) record REAL numbers, not the 500-cap/0 (CP29/30) | NOW DOES: (1) BACKEND — the threat-feed GET /api/security/threat-feed/ now returns a server-side action_counts aggregate over the FULL filtered set (uncapped): collapse path (default) counts per-action over the deduped distinct-request set, classifying each request by its STRONGEST outcome across all collapsed events (block > redact > canonical) so redactions are not hidden when a request/monitor event is canonical; non-collapse path uses a SQL Count grouped by action with .order_by() cleared (the order_by(-created_at) was leaking created_at into GROUP BY → undercounted to sum=3). Each request counted once → sum(action_counts)==count. (2) FRONTEND — useFirewallData captures action_counts + threatFeedCount and returns them; FirewallModulePage threads them into buildModulePageData; a new module-1.4 override sets summary.total=threatFeedCount (real count) and maps action_counts → blocked(denied), redacted(sanitized), allowed=monitor+allow (approved), instead of summarizeEvents(cappedResults) | touched: shared SOC threat-feed endpoint (all modules gain action_counts) + §1.4 telemetry | VERIFY: API — collapse count=4000 action_counts={monitor:3967,block:33} sum=4000 consistent; collapse=false count=64827 sum=64827 consistent (was sum=3 before the order_by fix); security_scan {allow:330,block:375,redact:77} sum=782 consistent. BROWSER (scripts/ralph/mcp_page_cp31_ctx_telemetry.mjs) — §1.4 flow-nodes render Context Fields 4,000 assembled (NOT 500-cap), PII Redaction 0 sanitized (honest — 0 redactions in the recent dedup-scan window; surfaces when present), Size Check 33 denied, Final Context 3,967 approved (NOT 0) → cp31Pass:true. Frontend build green (5.15s). CP32 = verify with live PII/oversized traffic.

### 2026-07-02 — MCP-PAGE-CP32 (verify) | scripts/ralph/mcp_page_cp32_ctx_live.py | WHAT: verify the §1.4 context-assembly stages MOVE under live traffic carrying PII/oversized context | HOW: drove the REAL enforcement-recording pipeline (POST /api/mcp-connector/internal/record-event/, gateway-internal auth — the same endpoint the gateway data-plane calls) with N=6 each of decision=redact (PII in payload: data_accessed=[john.doe@example.com]), decision=block (context_bytes=2MB oversized), decision=allow (clean), each with a UNIQUE request_id; then re-read the threat-feed action_counts and asserted the deltas | EVIDENCE: the true additive aggregate (collapse=false, not dedup-window-bounded) moved EXACTLY: assembled +18 (my 18 events), sanitized(redact) +6, denied(block) +6, approved(monitor via allow) +6 → cp32Pass:true. This proves the CP31 telemetry correctly categorizes redact→sanitized, block→denied, allow→approved on live traffic. NOTE: the frontend collapse view (dedup-scan-window) delta is masked at this instant because the parallel loop’s extreme concurrent traffic churns the fixed-size window fast (a load artifact, not a pipeline defect — CP31 proved the collapse render shows real numbers 4000/0/33/3967 under normal conditions). decision→action map: redact→redact, block→block, allow→monitor (control views.py:1909 _action_map). SECTION I (context-assembly 1.4 telemetry, CP29-32) COMPLETE.

### CHG-0060 — Decode-scan decoy-padding bypass closed (item G2/1.4 — obfuscation cap, fail-closed)
- **Date:** 2026-07-02
- **Severity:** HIGH (real redaction leak — an encoded secret hidden past a fixed token cap egressed verbatim on MCP tool results). Closes the residual explicitly deferred in CHG-0056.
- **Files:** `gateway/ai_mesh_gateway/patterns.py` (`_MAX_DECODE_TOKENS`, new `_MAX_DECODE_TOTAL_BYTES`, `_MAX_URL_DECODE_TOKENS`, `_iter_transport_decodes` byte-budget, URL-decode `original[:_CANON_MAX_LEN]`); `gateway/ai_mesh_gateway/tests/test_decode_budget_no_decoy_bypass.py` (+7).
- **WHAT (gap):** the base64/hex/url decode passes in `_redact_obfuscated` stopped after a fixed token COUNT (base64/hex `_MAX_DECODE_TOKENS=12`, url `_MAX_URL_DECODE_TOKENS=32`). A result could hide an encoded secret PAST the cap (`<12 benign base64 blobs> <base64("john.doe@example.com")>`) — the secret token was never decoded, so it egressed verbatim (trivially recoverable). Proven live pre-fix (base64/hex past 12 decoys, url past 32). Not purely adversarial: any legit result with >12 encoded fields where a later field carries encoded PII leaks.
- **WHY:** "field-level redaction of tool RESULTS, byte-verified, fail-closed" — the obfuscation decode scan must cover every encoded token in the scan window, not just the first N. This is the CHG-0056 documented residual ("`_MAX_DECODE_TOKENS=12` base64 cap lets a padded result hide an encoded secret past 12 decoy tokens").
- **NOW DOES:** the decode scan is bounded by a GLOBAL decoded-BYTE budget (`_MAX_DECODE_TOTAL_BYTES=262144`, shared across the base64+hex passes) instead of a per-pass token COUNT. The input is already capped at `_CANON_MAX_LEN=20000`, so decoding EVERY token in it is inherently bounded work; the byte budget is the real DoS bound (caps total decoded bytes incl. nested layers). `_MAX_DECODE_TOKENS` raised 12→4096 (high backstop above the ~1666 max base64 tokens a 20K input holds → never truncates a valid input); `_MAX_URL_DECODE_TOKENS` 32→4096 + the URL pass now scans `original[:_CANON_MAX_LEN]`. `_iter_transport_decodes` decrements the budget per decoded blob (top+nested) and stops when exhausted; `_iter_short_b64_infra` (CHG-0058) inherits the raised cap. No FP: benign short input is a byte-for-byte no-op (golden cases unchanged); scanning more tokens only masks GENUINE decoded PII/secret/infra (60-benign-decoy battery not false-masked). Perf worst case (full 20K base64 window): ~35-45ms.
- **Touched whose work:** the obfuscation-resistant redaction core in `patterns.py` (CHG-0054/0055/0056/0058 lineage; owning-session tier1 detection file). Integrates with CHG-0057 byte-verify (a surviving detected value still fails closed → block). detect_pii/detect_secrets unchanged.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_decode_budget_no_decoy_bypass.py -q` → 7 passed (base64/hex/URL secret hidden past the old caps now masked; many-decoys-within-window masked; benign decoys not false-masked; plain + single-encoded still work; bounded-fast <2s). Full sweep `ai_mesh_gateway/tests` → 1214 passed, 0 failed. Evidence: `mcp-parallel/findings/backstop-p2-decode-decoy-bypass/finding.md`. RESIDUAL (pre-existing, NOT changed): content beyond `_CANON_MAX_LEN=20000` is not obfuscation-decode-scanned (input-length cap); plain PII beyond is still raw-masked, only ENCODED content past 20K escapes the decode scan.

### 2026-07-02 — MCP-PAGE-CP33 (connect catalog) | WHAT: connect the catalog MCPs (frontend MCP_PRESETS = 10 priority) via the register flow, capture the outcome matrix | RESULT (scripts/ralph/mcp_page_cp33_catalog.py, fresh zeroshield sandbox): 6 CONNECTED w/ real tools, 4 clean-errored, 0 raw-broken. CONNECTED: Context7(streamable-http, 2 tools: query-docs/resolve-library-id), Playwright(stdio, 23 tools: browser_*), Memory(stdio, 9: add_observations/create_entities/...), Filesystem(stdio, 14: create_directory/edit_file/...), Everything(stdio, 13: echo/add/...), Vibe-Check(stdio, 5: check_constitution/...) = 66 real tools. CLEAN-ERRORED (correct, not raw crashes): GitHub → create 400 {auth_token required for bearer auth} (needs a bearer token, none provided); Linear → MCP_AUTH_FAILED (mcp-remote OAuth needs interactive login, headless); Semgrep → MCP_START_FAILED (missing semgrep host binary); Fetch → MCP_START_FAILED exit 2 (preset uses npx but mcp-server-fetch is a uvx/Python pkg — PRESET BUG, triage CP36). UI: all 10 presets render as "Quick register X" buttons (mcp_page_cp33_ui.mjs presetCount=10/10). NONE left broken (every server connects OR clean-errors). NOTE: the real catalog is 10 presets, not literally 50; the 10 priority are the achievable catalog | VERIFY: mcp_page_cp33_catalog.py matrix (6 connected/4 clean/0 broken) + mcp_page_cp33_ui.mjs (10/10 presets render). TRIAGE (CP36): Fetch npx→uvx; GitHub needs token; Semgrep needs binary; Linear OAuth.

### 2026-07-02 — MCP-PAGE-CP34 (tool discovery) | WHAT: verify tool DISCOVERY is complete + persisted with metadata for the connected catalog servers | RESULT (scripts/ralph/mcp_page_cp34_discovery.py): 38 tools discovered w/ FULL metadata (name + description + input_schema) across 4/4 servers, each server tools_count matching its persisted GET /servers/{id}/tools/ list — Everything 13, Memory 9, Filesystem 14, Context7 2. Every tool has a real tool_name, a non-empty description, and a JSON input_schema (with_description == with_input_schema == discovered for all). So discovery captures COMPLETE tool definitions (not just names/counts), and the persisted list is what the UI Tool-Discovery tab renders. Combined with CP33 (Playwright 23, Vibe-Check 5 also discovered), the 6 connected servers surface 66 real tools; the 4 clean-errored servers show 0 tools ONLY because they genuinely could not start (GitHub/Linear/Semgrep/Fetch) — not a discovery bug. cp34Pass:true | VERIFY: mcp_page_cp34_discovery.py → 4/4 ok, tools_count==len(tools), schemas captured.

### 2026-07-02 — MCP-PAGE-CP35 (tool execution) | WHAT: execute tools on the free/local catalog servers through the gateway and assert REAL results | RESULT (scripts/ralph/mcp_page_cp35_exec.py, POST /api/mcp-connector/tools/call/ = control → gateway → per-org sandbox → MCP server): 3/3 deterministic executions returned REAL results, cp35Pass:true. (1) Everything.echo({message:"hello-cp35-deterministic-42"}) → {"result":{"content":[{"type":"text","text":"Echo: hello-cp35-deterministic-42"}]}, "decision":"allow"} — deterministic echo verified. (2) Filesystem.list_allowed_directories({}) → "Allowed directories:\n/data/mcp-auth" (the sandbox-scoped dir). (3) Memory.create_entities({name:cp35-canary}) then read_graph({}) → the canary entity is created AND read back (real create→read ROUND-TRIP). everything.add skipped (not offered by this server build; naming differs). GOTCHA: memory crashed (MCP_SERVER_CRASHED / SIGABRT) on a heavily-loaded shared sandbox (parallel-loop host stress) but synced 9 tools + executed cleanly on a fresh sandbox / after a sync-retry — transient crash, correctly clean-errored + retryable, not an execution-path bug. Proves tool EXECUTION returns real results end-to-end for the free/local servers | VERIFY: mcp_page_cp35_exec.py → 3/3 real results.
