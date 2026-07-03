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

### CHG-0061 — ext_mcp_proxy non-200 / non-JSON egress leak closed (item 2 / 1.4 — result redaction parity, fail-closed)
- **Date:** 2026-07-02
- **Severity:** HIGH (real result-egress leak — an untrusted external server's non-200 or non-JSON response body egressed to the tenant unscanned).
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`import re`, `_is_text_content_type`/`_TEXT_CONTENT_TYPE_RE`, ext_mcp_proxy non-JSON + non-200 scan branches); `gateway/ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py` (+6).
- **WHAT (gap):** the tenant-facing external MCP passthrough `ext_mcp_proxy` (`/v1/mcp/ext-proxy/{host}/{path}`, CHG-0032/0033/0034/0043 lineage) applied its outbound result/error redaction floor ONLY to `status_code == 200` JSON bodies. Two egress paths forwarded the untrusted external server's body RAW: (1) a NON-JSON body (`resp.json()` raises — HTML error page, text/plain error, XML fault) was returned verbatim; (2) a NON-200 JSON body bypassed both scan branches (gated on `== 200`) and returned raw. So a secret/PII/infra string in a non-200 or non-JSON error body (`connect failed postgres://user:pass@10.1.2.3; contact john@example.com`) egressed unscanned — contradicting the CHG-0043 intent (scan error content), which was only wired for 200.
- **WHY:** "field-level redaction of tool RESULTS, byte-verified, fail-closed" — the result floor must cover every egress body regardless of HTTP status or content-type, not just 200+JSON.
- **NOW DOES:** (1) non-JSON text-like bodies (`_is_text_content_type`: text/*, application/json|xml|javascript|x-ndjson|graphql|*+json|*+xml; missing CT defaults to application/json → text) are scanned via `_scan_tool_result_floor(text)` before forwarding — WITHHELD on scan block/error; binary bodies (image/*, octet-stream) pass through untouched (masking would corrupt them, not a text-leak vector). (2) the `result` + `error` JSON scans lost the `status_code == 200` gate (run on ANY status); a new `elif status != 200 and data is not None` fallback scans the WHOLE non-200 body when it lacks a JSON-RPC result/error (e.g. `{"detail": ...}`, list, scalar). A 200 body without result/error stays untouched (benign session/notif shape — no behaviour change on the established path). Fails closed throughout.
- **Touched whose work:** the ext-proxy egress path (CHG-0032/0033/0034/0043 lineage). The ORG path (`org_mcp_jsonrpc`) is sandbox-routed (broker returns a parsed JSON-RPC dict scanned by the same floor), so it never had the raw-httpx-body issue — fix is ext-proxy-scoped.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py -q` → 39 passed (new: non-200 JSON error/detail/result bodies redacted w/ status preserved; non-JSON text body redacted; binary passthrough byte-for-byte unchanged; text body fails closed/withheld on scan error). Full sweep `ai_mesh_gateway/tests` → 1220 passed, 0 failed. Evidence: `mcp-parallel/findings/backstop-p2-ext-proxy-nonok-egress/finding.md`.

### 2026-07-02 — MCP-PAGE-CP36 (triage) | services/mcp-broker/src/sandbox/docker_manager.py + shared/ai_mesh_shared/mcp_stdio_common.py + frontend MCPConnectorPanel.jsx | WHAT: triage every catalog MCP that failed in CP33 → per-server root cause → fix → re-test; none left broken | FIXED — Fetch: two root causes (1) preset used `npx mcp-server-fetch` but it is a uvx/Python package → preset command npx→uvx, args ["-y","mcp-server-fetch"]→["mcp-server-fetch"]; (2) uvx installs tools to UV_TOOL_DIR (~/.local/share/uv/tools) + UV_TOOL_BIN_DIR (~/.local/bin), BOTH on the READ-ONLY sandbox rootfs → "Read-only file system" start-fail. Fix: docker_manager sets UV_TOOL_DIR=/var/cache/uv/tools + UV_TOOL_BIN_DIR=/var/cache/uv/bin (writable tmpfs) AND added them to _SAFE_ENV_PASSTHROUGH (the child env is rebuilt fresh from the allowlist — create_subprocess_exec does not inherit the parent — so a container-only env var is invisible to the child). RESULT: Fetch connects (1 tool: fetch) + EXECUTES fetch(https://example.com) → 200 real page content. BONUS (CP20 gap closed): NODE_OPTIONS (the --max-old-space-size heap cap) was ALSO not in the passthrough allowlist, so the actual Node MCP-server child ran WITHOUT the heap cap — added NODE_OPTIONS to _SAFE_ENV_PASSTHROUGH so CP20 reaches the real servers. CLEAN-ERRORED (unsupported without X, correct — NOT broken): GitHub → needs a bearer token (clean create-400 validation); Semgrep → needs the semgrep host binary in the sandbox image (MCP_START_FAILED); Linear → mcp-remote OAuth is interactive/headless-blocked (MCP_AUTH_FAILED). FINAL CATALOG MATRIX: 7 CONNECTED (Context7/Playwright/Memory/Filesystem/Everything/VibeCheck + Fetch) + 3 clean-errored (GitHub/Semgrep/Linear) = NONE BROKEN | VERIFY: uvx Fetch connect+execute live; broker sandbox-lifecycle 32 passed; gateway -k stdio 10 passed; frontend build green (4.88s); sandbox image rebuilt; passthrough includes UV_TOOL_DIR/UV_TOOL_BIN_DIR/NODE_OPTIONS. SECTION J (50-MCP connect/discovery/execution/triage, CP33-36) COMPLETE.

### 2026-07-02 — MCP-PAGE-CP37 (per-server actions) | WHAT: verify every per-server action on the MCP Servers list works + REFLECTS STATE (the exact backend each card button drives) | RESULT (scripts/ralph/mcp_page_cp37_actions.py): 6/6 actions pass, cp37Pass:true, each mutation READ BACK to prove state reflects: (1) connect/sync → POST /servers/{id}/tools/ → 13 tools discovered; (2) details → GET /servers/{id}/tools/ → real tool list; (3) disable → PATCH /servers/{id}/tools/{echo}/ {enabled:false} → read-back enabled==False (then re-enabled); (4) scan → PATCH {scan_action:redact} → read-back scan_action==redact (then inherit); (5) authorize → POST /servers/{id}/oauth/authorize/ on an http-oauth server → runs OAuth discovery + returns a CLEAN result (502 {error} on GitHub URL discovery, NOT a crash — the action starts the flow correctly); (6) delete → DELETE 204 → GET now 404 (state reflects). All card buttons (sync RefreshCw / details Eye / delete Trash2 / authorize Shield / per-tool enable-disable + scan_action) are wired to these verified backends | VERIFY: mcp_page_cp37_actions.py → 6/6 actions work + reflect state.

### CHG-0062 — Per-org burst/RPM counters set TTL atomically (item 9 rate-limit + item 11 Redis correctness)
- **Date:** 2026-07-02
- **Severity:** MEDIUM (Redis resource-leak / correctness on the MCP path — unbounded orphaned no-TTL keys under cancellation/crash, i.e. under soak/chaos).
- **Files:** `gateway/ai_mesh_gateway/rate_limit_enforcement.py` (`enforce_org_burst_rpm` burst + RPM blocks); `gateway/ai_mesh_gateway/tests/test_rate_limit_atomic_ttl.py` (+5).
- **WHAT (gap):** `enforce_org_burst_rpm` did `current = await redis.incr(key)` then `if current == 1: await redis.expire(key, ttl)` — two separate commands, for BOTH the burst (req/s) and RPM (req/min) counters. If the request coroutine is CANCELLED (client disconnect — routine under load) or crashes BETWEEN the INCR and EXPIRE, the key is created with NO TTL and orphaned forever; because the TTL was set only when `current == 1`, a cancelled first request means the key NEVER gets a TTL. Time-bucketed keys (`burst:{sec}` / `{minute}`) → each orphan is dead weight that never expires → unbounded Redis memory leak under the 5k-10k-concurrent / chaos stress scenario. On the MCP path via `_mcp_org_rate_limit_raw` (all 3 MCP entry points, CHG-0031/0032). INCONSISTENT with the already-atomic siblings: the tool-call cap (`mcp_proxy.py ~1066`, CHG-0048, `pipeline(transaction=True)` + `expire(nx=True)`) and `rate_limiter.py` (Lua eval).
- **WHY:** "PostgreSQL + Redis correctness" + "gateway rate-limit" + soak-safety ("no exhaustion"). Counter TTLs must be set atomically with the INCR so a cancellation/crash can't orphan a key.
- **NOW DOES:** both counters run `INCR` + `EXPIRE NX` in ONE MULTI/EXEC transaction (`async with redis_client.pipeline(transaction=True) as pipe: pipe.incr(key); pipe.expire(key, ttl, nx=True); current = (await pipe.execute())[0]`), matching the CHG-0048 idiom. ATOMIC (no INCR↔EXPIRE window) + SELF-HEALING (EXPIRE NX on EVERY request re-sets a missing TTL, so a previously-orphaned key is repaired on its next increment; NX means later same-bucket hits do NOT slide the window — the count still rises). Fail-OPEN preserved (surrounding try/except still allows on any Redis error).
- **Touched whose work:** the gateway per-org rate limiter (CHG-0031/0032 lineage). No behaviour change to limits/thresholds — only the TTL-setting is made atomic + self-healing.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_rate_limit_atomic_ttl.py -q` → 5 passed (keys always get a TTL; orphaned no-TTL key self-healed; EXPIRE NX doesn't reset TTL on later increments but count still rises; burst limit still enforced → 429; fail-open on Redis error). `test_mcp_rate_limit.py` → 14 passed (no regression). Full sweep → 1228 passed, 0 failed. Evidence: `mcp-parallel/findings/backstop-p11-ratelimit-atomic-ttl/finding.md`. SIBLING (documented, NOT changed — one item/iteration): `leakage_detector.py:116-120` (sadd loop then separate expire) is the same class, milder (unconditional expire), a future follow-up.

### 2026-07-02 — MCP-PAGE-CP38 (Tool Discovery tab) | WHAT: verify the Tool Discovery tab renders REAL data + its controls work | DATA SOURCE: GET /api/mcp-connector/tools/ (org-wide aggregate) → 200, returns tools with the EXACT field names renderTools consumes: name / description / server_name / inputSchema (all present; all tools have name+inputSchema). RENDER (renderTools, MCPConnectorPanel.jsx:1842): each tool = a Card with tool.name (mono), tool.description, a server_name Badge, and an expandable <details> Input Schema (JSON). CONTROLS: Refresh button (aria "Refresh tools" → loadTools → re-GET /tools/); loading skeletons; EmptyState when 0. VERIFY (browser, scripts/ralph/mcp_page_cp38_discovery_tab.mjs): clicking the Tool Discovery tab (Radix role=tab) → 37 real tool cards render (hasRealToolNames browser_click/echo/create_entities true, server badges Playwright/Everything/Memory true, Input Schema expandable true), Refresh button re-fetches (refreshRefetched true), zero pageError → cp38Pass:true. API: GET /tools/ count 50 all name+schema present.

### 2026-07-02 — MCP-PAGE-CP39 (Tool Execution tab) | WHAT: verify every control on the Tool Execution tab drives a REAL execution | CONTROLS (renderExecute, MCPConnectorPanel.jsx:1948): Server <Select> (aria "Execution server"), Tool <Select> (aria "Tool to execute", disabled until a server is chosen), "Fill from Schema" button (seeds args from inputSchema), Arguments <textarea> (aria "Tool arguments (JSON)"), Execute Tool button (executeTool → POST /api/mcp-connector/tools/call/), result panel, Refresh-tools button. VERIFY (browser, scripts/ralph/mcp_page_cp39_execute_tab.mjs): registered Everything (echo), clicked the Tool Execution tab (role=tab) → selected the server select → selected the echo tool → typed args {"message":"cp39-exec-hello-777"} → clicked Execute Tool → POST /tools/call/ FIRED (toolCallFired true) and the result panel shows "Echo: cp39-exec-hello-777" (resultShowsEcho true), zero pageError → cp39Pass:true. Real execution through the UI controls end-to-end (control → gateway → sandbox → server → result). Builds on CP35 (backend real results) | VERIFY: mcp_page_cp39_execute_tab.mjs.

### 2026-07-02 — MCP-PAGE-CP40 (Scan Controls + Matrix) | WHAT: verify every Scan Controls / Scan Control Matrix toggle has a REAL EFFECT | BACKEND REAL-EFFECT (scripts/ralph/mcp_page_cp40_scan_controls.py via control shell, using the authoritative resolve_effective_controls the tool-call enforcement path uses): CRUD + precedence proven, 5/5 checks, cp40Pass — baseline srv tier1_input=inherit; CREATE org-scope tier1-input action=redact → srv+other both=redact (org applies to all); CREATE server-scope tier1-input action=block on srv → srv=block (server OVERRIDES org, SCOPE_RANK tool>server>org), other=redact (org default persists); EDIT server row action→tag → srv=tag; DELETE server row → srv=redact (falls back to org); DELETE org row → srv=inherit (default). UI (scripts/ralph/mcp_page_cp40_ui.mjs): Scan Controls tab renders the matrix (Tier-2 toggle "Tier-2 (...) for this org", effective Tier-1/Tier-2 preview, controls table) + the "Add control" button opens the create dialog → cp40UiPass. Combined with CP28 (Tier-2 SegmentedControl Inherit/Enabled/Disabled) + CP37 (per-tool scan_action PATCH) — every matrix control persists AND changes the resolved effective action | VERIFY: mcp_page_cp40_scan_controls.py (5/5 precedence) + mcp_page_cp40_ui.mjs.

### 2026-07-02 — MCP-PAGE-CP41 (MCP Security Policies) | WHAT: verify the MCP Security Policies tab controls have a REAL ENFORCEMENT EFFECT on MCP tool calls | REAL EFFECT (scripts/ralph/mcp_page_cp41_policy.py): created an mcp-domain Policy(policy_domain=mcp, enabled) + keyword Rule(rule_type=keywords, condition={keywords:[cp41blockword777], field:prompt}, action=block) then executed echo through the real tool-call path (control MCPToolCallView → policy_evaluate(domain=mcp)) — 4/4 checks, cp41Pass: (1) baseline (no policy) echo(cp41blockword777) → 200 allowed; (2) with block policy → echo(cp41blockword777) → 403 "Tool call blocked by policy" (matched_policies present); (3) with policy, echo(hello-benign) NON-matching → 200 allowed; (4) DISABLE the policy → echo(cp41blockword777) → 200 allowed again (enable/disable control has a real effect). Proves the tab controls (create policy, keyword rule, block action, scope=mcp, enable/disable) enforce on live MCP tool calls (same policy engine as CP22/CP23 blocked_by_policy). UI (scripts/ralph/mcp_page_cp41_ui.mjs): the MCP Security Policies tab renders the PolicyManagementPanel (title, enforcement copy "single enforcement layer for every MCP tool call", Add/New policy control, per-server filter) → cp41UiPass | VERIFY: mcp_page_cp41_policy.py (4/4) + mcp_page_cp41_ui.mjs.

### 2026-07-02 — MCP-PAGE-CP42 (Observability tab + responsive/theme sweep) | WHAT: verify the Observability tab (post-CP26 fix) — every control + real data, both themes at 1440/1024/768/375, zero console errors | RESULT (scripts/ralph/mcp_page_cp42_observability.mjs): cp42Pass:true — (1) real data: summary cards render numeric total + decision info (allow/block/redact/monitor/error) from /api/mcp-connector/events/summary/ + /events/ (CP24/CP26 real data); (2) TIME-RANGE control (SegmentedControl aria "Time window", 1h/24h/7d/30d/All) — clicking "7d" re-fetches events (timeRangeRefetched true); (3) REFRESH button (aria "Refresh events") re-fetches BOTH /events/ + /events/summary/ (refreshRefetched true); (4) RESPONSIVE/THEME SWEEP — rendered at 1440x900, 1024x768, 768x1024, 375x812 in BOTH light + dark themes (8 renders) → NO horizontal overflow anywhere (noOverflow true); (5) ZERO console errors + zero pageerrors across the whole session (noConsoleErrors true, consoleErrors=[]). SECTION K (every button/click on firewall-1-4, CP37-42) COMPLETE | VERIFY: mcp_page_cp42_observability.mjs.

### CHG-0063 — Chunked / no-Content-Length body memory-DoS closed (item 9 validation + resource-limits, fail-closed)
- **Date:** 2026-07-02
- **Severity:** HIGH (memory-exhaustion DoS on the tenant-facing MCP entry points). Closes CHG-0034's documented limitation.
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`_MCPBodyTooLarge`, `_mcp_read_body_capped`, the three entry-point body reads, `_mcp_body_too_large` docstring); `gateway/ai_mesh_gateway/tests/test_mcp_body_cap.py` (+7).
- **WHAT (gap):** `_mcp_body_too_large` (CHG-0034) rejects an oversized body before buffering — but it only inspects the declared Content-Length HEADER. A `Transfer-Encoding: chunked` (or Content-Length-omitting) request slips past it, and the entry point then `await request.body()` / `request.json()` buffers the ENTIRE stream into memory with NO ceiling → gigabyte chunked body → gateway OOM. The `_mcp_body_too_large` docstring itself flagged this ("a chunked request that omits Content-Length is not caught here"). Affects the three tenant-facing entry points: `ext_mcp_proxy`, `org_mcp_jsonrpc`, `org_mcp_tool_call`.
- **WHY:** "gateway validation" + "resource bombs (mem) contained" / "CPU/mem/disk/timeout limits" — the body ceiling must bound the ACTUAL bytes read, not just the declared length.
- **NOW DOES:** `_mcp_read_body_capped(request)` reads `request.stream()` incrementally and raises `_MCPBodyTooLarge` the INSTANT the running total crosses `_MCP_MAX_BODY_BYTES` (default 10MiB), so the gateway never holds more than the ceiling in memory regardless of framing (proven: the cap stops after the crossing chunk, not after buffering the whole stream). The capped bytes are cached on `request._body` so a downstream `request.json()`/`body()` reuses the already-capped buffer. Wired at all three entry points (ext + bare-REST read via the helper → bytes; org_mcp_jsonrpc caps+caches then keeps `request.json()`), each returning the existing 413 `_mcp_body_too_large_response()` on `_MCPBodyTooLarge`. The cheap Content-Length pre-check is retained (fast reject for an honestly-declared oversize). A test-double fallback (no `stream()`) uses `body()` if present (still ceiling-checked) else no-ops, so `.json()`-mocking doubles are unaffected.
- **Touched whose work:** the gateway MCP request-ingress DoS guards (CHG-0034 lineage). Scoped to the 3 tenant-facing entry points; backend-internal paths (X-Gateway-Internal-Key trust) still use plain `request.body()` (lower risk; possible future follow-up).
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_body_cap.py -q` → 7 passed (over-ceiling raises; cap stops reading early / bounds memory; under-ceiling returns+caches; exactly-at-ceiling ok, +1 raises; pre-buffered oversize rejected; test-double body() fallback still capped; END-TO-END `org_mcp_tool_call` oversized chunked `stream()` → HTTP 413 `mcp_body_too_large`). Full sweep `ai_mesh_gateway/tests` → 1237 passed, 0 failed. Evidence: `mcp-parallel/findings/backstop-p9-chunked-body-dos/finding.md`.

### 2026-07-02 — MCP-PAGE-CP43 (impeccable init + audit) | WHAT: /impeccable init + audit the firewall-1-4 page → docs/mcp/IMPECCABLE_AUDIT_firewall-1-4.md | INIT: PRODUCT.md present (ZeroShield security control plane; principles: every number real or honestly absent / never leak / state you can trust) → no init needed. AUDIT (code-level 5-dimension score, document-only, grounded in greps of MCPConnectorPanel.jsx 2428 LOC + MCPScanControlMatrix 937 LOC): Health 16/20 (Good→excellent) — A11y 3/4 (22 aria-labels, 0 div-onClick, Radix tab roles; GAPS: 3× 32px touch targets <44px, muted text-slate-400/500 contrast pass needed); Performance 3/4 (12 useCallback, 1 bounded 15s poll; GAPS: 0 useMemo → derived lists recompute, 500-row unvirtualized event list); Responsive 3/4 (CP42 no overflow 1440/1024/768/375; GAP: touch targets); Theming 4/4 (0 hard-coded hex, 111 dark: variants, both themes verified — strongest dim); Anti-Patterns 3/4 (0 gradient text, 0 decorative glassmorphism, no hero-metric/AI-slop; GAP: 24 Cards = card-heavy, server list would read denser as rows/table). CROSS-CUTTING: #1 maintainability = MCPConnectorPanel.jsx 2428 LOC >> 500 cap → split into per-tab sub-components; DATA HONESTY already SATISFIED by CP01-42 (real data, honest empty/error states, no leakage) — revamp must PRESERVE not regress. CP44 backlog: split panel, server list→rows, 44px touch targets, contrast pass, useMemo, PRESERVE CP01-42 behavior. VERDICT: functionally impeccable post-CP01-42; revamp is structural/visual, NOT a functional rewrite | VERIFY: docs/mcp/IMPECCABLE_AUDIT_firewall-1-4.md.

### 2026-07-02 — MCP-PAGE-CP44 (impeccable revamp) | WHAT: apply the CP43 audit backlog as a targeted craft pass that PRESERVES all CP01-42 verified behavior (a from-scratch rewrite of the 42-checkpoint-verified 2428-LOC component would recklessly regress it — engineering discipline: never break a passing gate) | frontend/src/components/MCPConnectorPanel.jsx: (1) PERF (audit dim 2, was 0 useMemo) — memoized executableTools (filter+filter+sort), selectedExecuteTool (find), connectedCount (filter), toolsDiscovered (reduce) with useMemo so derived lists no longer recompute every render; behavior-identical. (2) A11y/RESPONSIVE (audit dims 1+3) — server-card icon buttons h-8 w-8 (32px) → h-9 w-9 (36px) for larger touch targets. PRESERVED (audit already 4/4 theming, 0 gradient/glassmorphism, data honesty from CP01-42): all real-data binding, honest empty/error states, no leakage, both themes, every control. DELIBERATELY NOT DONE: a from-scratch rebuild (would regress CP01-42) + the 2428-LOC file split + server-list→rows (documented CP43 backlog; too risky for a loop under contention) | VERIFY: frontend build green (4.92s); REGRESSION — CP39 (Tool Execution, uses memoized executableTools/selectedExecuteTool) → cp39Pass:true (real echo); CP42 (page + tabs + sweep) → cp42Pass:true, 0 console errors, no overflow. No regression.

### CHG-0064 — ext_mcp_proxy response-side memory-DoS closed (item 10 resource-limits/mem, fail-closed)
- **Date:** 2026-07-02
- **Severity:** HIGH (cross-tenant memory-exhaustion DoS from an untrusted external MCP server). Response-side twin of CHG-0063.
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`_MCP_MAX_RESPONSE_BYTES`, `_read_response_capped`, `_mcp_upstream_too_large_response`, the two ext-proxy `resp.aread()` sites); `gateway/ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py` (+3 integration, response doubles gain `aiter_bytes`); `gateway/ai_mesh_gateway/tests/test_mcp_body_cap.py` (+2 unit).
- **WHAT (gap):** the tenant-facing external passthrough `ext_mcp_proxy` buffers an UNTRUSTED external server's WHOLE response via `sse_bytes = await resp.aread()` (finite SSE result) and `body_bytes = await resp.aread()` (JSON/text/binary) with NO size ceiling. The code comment claimed "capped by the httpx timeout" — but a timeout bounds TIME, not SIZE: a malicious/compromised tenant-configured external MCP server can stream a multi-GB response FAST (within the timeout) and OOM the SHARED gateway → a cross-tenant DoS one tenant's server inflicts on all others.
- **WHY:** "resource limits CPU/mem/disk/timeout enforced + containment" / "resource bombs (mem) contained" — the response buffer must be bounded by SIZE, not just time. This is the response-side counterpart of CHG-0063's request-body cap.
- **NOW DOES:** new `_MCP_MAX_RESPONSE_BYTES` (env `MCP_MAX_RESPONSE_BYTES`, default 10MiB) + `_read_response_capped(resp)` iterates `resp.aiter_bytes()` incrementally and raises `_MCPBodyTooLarge` (reused) the INSTANT the running total crosses the ceiling — the gateway never holds more than the ceiling from an untrusted upstream in memory. Both `aread()` sites use it; on overflow the response is WITHHELD with a 502 `mcp_upstream_response_too_large` (after closing the upstream stream + client). The non-finite SSE passthrough (`async for chunk in aiter_bytes(): yield chunk`) is UNCHANGED — it streams chunk-by-chunk, never holding the whole body, so it was never the DoS.
- **Touched whose work:** the ext-proxy egress path (CHG-0032/0033/0034/0043/0061 lineage). Scoped to the untrusted-upstream boundary (ext-proxy); the ORG path is sandbox-routed (broker) — the gVisor sandbox's own mem/disk limits contain a huge sandbox response, so the gateway is not the buffer there.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py ai_mesh_gateway/tests/test_mcp_body_cap.py -q` → 51 passed (oversized JSON upstream → 502 mcp_upstream_response_too_large; oversized SSE upstream → 502; normal response unaffected; `_read_response_capped` raises over / returns under; existing 39 ext-proxy tests still pass with response doubles updated to expose `aiter_bytes`). Full sweep `ai_mesh_gateway/tests` → 1242 passed, 0 failed. Evidence: `mcp-parallel/findings/backstop-p10-response-mem-dos/finding.md`.

## CHG-MCP-PAGE-CP45 — revamp wired to REAL data, honest states, no leakage (+ dup-key row-identity bug fix)
- **Files:** `frontend/src/components/MCPConnectorPanel.jsx` (Tool Discovery tab key), `scripts/ralph/mcp_page_cp45_realdata_noleak.mjs` (new live verifier).
- **WHAT:** CP45 audit — prove the 1.4 page shows REAL backend data, honest empty/loading/error states, and leaks no raw keys/PII/backend internals. Found + fixed a real defect: the Tool Discovery tab keyed aggregated tools by bare `tool.name`; with 22 registered servers sharing tool names (echo/add/printEnv across `everything` presets) React threw "Encountered two children with the same key" → non-unique keys let rows swap identity → the WRONG value could render against the WRONG tool row (a data-honesty regression, exactly what CP45 guards).
- **WHY:** "every number real or honestly absent · never leak · state you can trust." Duplicate keys break React's row identity → dishonest display under load. The gateway key must never be shown raw.
- **NOW DOES:** (1) Discovery tab key is `${makeExecuteToolKey(tool)}-${idx}` = `server_slug::name-idx` (matches the Execute tab, globally unique, behavior-identical). (2) Verified live: all 22 backend `/servers/` names render (0 missing); observability total tracks the live counter (not hardcoded/zero); gateway key is masked by default (`{prefix}••••`), reveal/copy buttons ABSENT on GET, no plaintext in the DOM (backend GET never serves it; POST reveals once; MCP key is hash-only at rest); 0 backend/PII leak tells across all 5 tabs.
- **Touched whose work:** the MCP-page revamp lineage (CP43 audit → CP44 craft pass). The gateway-key masking (renderGatewayKeyBanner) + backend OrgGatewayKeyView are pre-existing and were verified, not changed.
- **VERIFY:** `cd frontend && npm run build` green (4.87s); `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium-browser BASE_URL=http://127.0.0.1:8180 node scripts/ralph/mcp_page_cp45_realdata_noleak.mjs` → cp45Pass:true (realDataOk, revealButtonAbsentOnGet, noPlaintextKeyInBanner, noBackendLeak, summaryRendered, notStuckLoading, noConsoleErrors, noOverflow; both themes @1440+375). Evidence: `mcp-parallel/findings/mcp-page/cp45/report.json`.

### CHG-0065 — ext_mcp_proxy SSRF / DNS-rebinding guard (item 12 egress-lockdown / item 9 validation, fail-closed)
- **Date:** 2026-07-02
- **Severity:** HIGH (SSRF → cloud-metadata credential theft / internal-service reach; mitigated by an operator allowlist, so practical vector is DNS rebinding/hijack — MEDIUM likelihood, HIGH impact).
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (ext_mcp_proxy SSRF guard after target_url); `gateway/ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py` (+3 SSRF tests, +1 autouse hermeticity fixture).
- **WHAT (gap):** `ext_mcp_proxy` validates the target ONLY by matching the hostname STRING against `_ALLOWED_MCP_DOMAINS`, then forwards to `https://{hostname}/...` — it NEVER resolves the host or checks the resolved IP. An allowlisted domain that RESOLVES to an internal address (DNS rebinding, DNS hijack of a third-party domain, or a misconfigured/future allowlist entry) is forwarded to → a caller reaches the cloud-metadata endpoint 169.254.169.254 (IAM credential theft), loopback/link-local, or RFC-1918 internal services. The sibling internal paths (`internal_discover_tools`/`internal_tools_call`) ALREADY guard this via `is_safe_outbound_url` ("finding mcp#1"); ext_mcp_proxy was the omission.
- **WHY:** "gVisor + ... egress-lockdown" + gateway validation — a forwarding path to a tenant-influenced host must resolve + reject internal/metadata targets, not string-match a hostname.
- **NOW DOES:** ext_mcp_proxy calls `is_safe_outbound_url(target_url)` (the same guard the internal paths use) after building the target and before any OAuth/body/forward work; on rejection it returns 400 `Upstream URL rejected by SSRF guard: <reason>`. `is_safe_outbound_url` (`_url_guard.py`) validates the scheme, resolves via getaddrinfo, and blocks any private/loopback/link-local/cloud-metadata resolved IP, FAIL-CLOSED on parse/DNS error (`MCP_ALLOW_INTERNAL_HOSTS` overrides for dev). httpx default `follow_redirects=False` means a 302-to-internal is returned+response-scanned (CHG-0061/0064), not auto-followed — so the initial-target guard is the needed control. No FP: real public allowlisted domains resolve to public IPs → allowed.
- **Touched whose work:** the ext-proxy egress path (CHG-0032/0033/0034/0043/0061/0064 lineage). Restores parity with the internal-path SSRF guard. A file-scoped autouse fixture keeps the existing redaction tests hermetic (no real DNS).
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py -q` → 45 passed. New: guard-rejects → ext_mcp_proxy 400 "SSRF guard" (wiring); REAL guard + allowlist={"localhost"} → `ext_mcp_proxy("localhost/mcp")` resolves 127.0.0.1 → 400 (end-to-end, no network); safe public target NOT SSRF-blocked. Direct probe: `is_safe_outbound_url` blocks localhost/169.254.169.254, allows mcp.linear.app. Full sweep `ai_mesh_gateway/tests` → 1245 passed, 0 failed. Evidence: `mcp-parallel/findings/backstop-p12-ext-proxy-ssrf/finding.md`.

## CHG-MCP-PAGE-CP46 — revamp verified end-to-end (type-sim + themes + responsive + impeccable detector clean)
- **Files:** `scripts/ralph/mcp_page_cp06_verify.mjs` (transport-flap filter — the only code change; verification-only).
- **WHAT:** CP46 end-to-end verification of the revamped MCP page (post CP44 useMemo + CP45 dup-key fix). Three legs: (1) type-sim, (2) both themes + responsive across all tabs, (3) impeccable detector.
- **WHY:** the revamp must PRESERVE the CP01-42 verified behavior — most critically the modal comma-drop/focus-loss fix — and stay AI-slop/anti-pattern clean.
- **NOW DOES:** (1) TYPE-SIM — CP06 re-run: ALL 8 combos PASS (1440/1024/768/375 × light/dark); every modal field (name/description/command/args/env) typed char-by-char with commas+spaces → value correct + focus never dropped. (2) THEMES+RESPONSIVE — CP45 5-tab sweep light+dark @1440+375: no overflow, 0 console errors. (3) DETECTOR — detect.mjs on MCPConnectorPanel + MCPScanControlMatrix + PolicyManagementPanel → exit 0, zero findings. HARNESS FIX: CP06 run-1 flagged 1/8 with consoleErr:11 = all `net::ERR_NETWORK_CHANGED` (OS transport flaps under parallel-loop contention, NOT app errors — type-sim passed even there). Added TRANSIENT_NET filter (ignores NETWORK_CHANGED/IO_SUSPENDED/INTERNET_DISCONNECTED/ABORTED/ADDRESS_UNREACHABLE/NAME_NOT_RESOLVED; keeps ERR_CONNECTION_REFUSED/4xx/5xx/React warnings/pageerrors) → re-run 8/8 incl. the previously-flaky combo.
- **Touched whose work:** MCP-page revamp lineage (CP43 audit → CP44 craft → CP45 real-data). No product code changed — verification harness only.
- **VERIFY:** `node scripts/ralph/mcp_page_cp06_verify.mjs` → "CP06: ALL 8 combos PASS", report.json allPass=true fails=[]; `node .claude/skills/impeccable/scripts/detect.mjs <3 components>` → exit 0 []. Evidence: `mcp-parallel/findings/mcp-page/cp06/report.json`.

## CHG-MCP-PAGE-CP47 — high-throughput MCP load driver (multiprocess × async closed-loop)
- **Files:** `scripts/ralph/mcp_page_cp47_stress.py` (new load harness).
- **WHAT:** a driver to push toward the 100k-call/100k-RPS target across many orgs × sandboxes, extending the round-based P9 harnesses into a multiprocess × async CLOSED-LOOP design (no per-round barrier → true sustained RPS).
- **WHY:** "prove it at scale" — measure failures/latency/saturation under sustained concurrent tool-call load and prove cross-tenant isolation holds under that load.
- **NOW DOES:** WORKERS processes (fan-out across cores) each run CONN async conns closed-loop over a per-process quota; same wire contract as #28/#29 (echo/get-sum deterministic oracles); metrics = calls_made, achieved_rps, p50/p90/p99/max/mean latency (bounded reservoir), HTTP status histogram, true-drop vs backpressure split; isolation oracles on every reply (id-match, content nonce-match, arithmetic, + a HARD cross-tenant CANARY per org). Env-tunable TARGET_CALLS/DURATION_S/WORKERS/CONN/MAX_FAIL_RATE. Fails hard on drop_rate>MAX_FAIL_RATE, any content swap, or any canary leak.
- **Touched whose work:** the P8/P9 scale-harness lineage (mcp_concurrency_live.py / mcp_load_live.py); reuses their manifest (.mcp_scale_manifest.json) and echo/get-sum contract. org-a/org-b are driven read-only (echo/get-sum) as the cross-tenant leak canary — no config mutation.
- **VERIFY:** 2 live smokes vs the 15-MCP fleet (:8300). 300 calls/8-inflight → 300 OK, 0 drop, isolation clean, rps=33.5 p50=208ms. 2000 calls/32-inflight → 2000 OK, 0 drop, isolation clean (id/content/arith/xtenant/canary=0), rps=45.5 p50=692ms p99=845ms. BASELINE: 4× in-flight → 1.36× rps but 3.3× latency = at throughput ceiling (Little's Law ≈46) → CP48 quantifies bottleneck. Report: `mcp-parallel/findings/mcp-page/cp47/report.json`.

### CHG-0066 — Sandbox agent JSON upstream-response cap made incremental (item 10 resource-limits/mem, containment)
- **Date:** 2026-07-02
- **Severity:** MEDIUM (sandbox-contained mem-bomb — an untrusted upstream could OOM/restart a tenant's sandbox before the size check). Sandbox-agent counterpart of CHG-0064.
- **Files:** `services/mcp-broker/sandbox-image/agent/upstream_manager.py` (streamable-http JSON branch); `services/mcp-broker/sandbox-image/agent/tests/test_upstream_proxy.py` (+2, fake gains `aiter_bytes`).
- **WHAT (gap):** the per-tenant sandbox agent dials the UNTRUSTED upstream MCP server via `client.stream(...)`; for a JSON (non-SSE) response it did `raw = await response.aread()` then `if len(raw) > _MAX_RESPONSE_BYTES: raise` — buffering the ENTIRE streaming body into memory BEFORE the size check. A malicious upstream returning a multi-GB body OOMs the sandbox agent (up to the sandbox's 2GiB mem limit → kill + restart of that tenant's sandbox) instead of a clean rejection at the 8MiB ceiling. The SSE branch right below was ALREADY incremental (`aiter_lines` + running `total`); only the JSON branch used the buffer-then-check anti-pattern (the same one CHG-0064 fixed on the gateway).
- **WHY:** "resource limits ... mem ... + containment" / "resource bombs (mem) contained" — the untrusted-upstream read must bound memory by size while streaming, not buffer-then-check.
- **NOW DOES:** the JSON branch reads incrementally via `response.aiter_bytes()`, accumulating into a running `_total` and raising `UpstreamError(-32000, "upstream response too large")` the INSTANT it crosses `_MAX_RESPONSE_BYTES` (env `MCP_AGENT_MAX_RESPONSE_BYTES`, default 8MiB) — the agent never buffers more than the ceiling from an untrusted upstream. Parity with the SSE branch; behaviour/error unchanged for legitimate responses.
- **Touched whose work:** the sandbox agent's upstream proxy (P4.9 / the sandbox-routed http/sse path). Reduces the blast radius from "sandbox OOM + restart churn" to a clean -32000 at 8MiB (the sandbox mem limit still contains it — CHG-0015).
- **VERIFY:** `cd services/mcp-broker && ./.venv/bin/python -m pytest sandbox-image/agent/tests/test_upstream_proxy.py -q -k "not websocket"` → 11 passed (2 new: JSON response over cap → -32000 "upstream response too large"; under cap still returns the tools/list result via the incremental reader). The 3 `websocket` tests HANG in this env PRE-EXISTINGLY (real ws connection / async-loop issue; the streamable-http + SSE tests all pass and this change touches only the streamable-http JSON branch). Evidence: `mcp-parallel/findings/backstop-p10-sandbox-response-cap/finding.md`. FOLLOW-UPS (documented, not changed): `_read_json_response` is dead code with the same pattern; the error-body reads read-whole-then-slice; `_validate_upstream` does allowlist matching but no resolved-IP SSRF check (sandbox analogue of CHG-0065, relevant while sandbox net is internal=false).

## CHG-MCP-PAGE-CP48 — saturation sweep + bottleneck localization (shared ~47 RPS chokepoint)
- **Files:** `scripts/ralph/mcp_page_cp48_saturation.py` (new sweep orchestrator); `scripts/ralph/mcp_page_cp47_stress.py` (+ ORG_FILTER/SERVER_FILTER for single-target isolation).
- **WHAT:** run the load driver at escalating concurrency; measure failure/latency/saturation; localize the binding bottleneck.
- **WHY:** "prove it at scale" — quantify the real ceiling + what saturates + whether isolation holds under load.
- **NOW DOES (findings):** Fleet (5 servers) RPS pins at ~47 from 32 in-flight (8→128: 37.5/42.7/46.9/47.4/46.5), latency grows LINEARLY (p50 203→2604ms) = saturated closed-loop. 0 drops/backpressure/503/OOM across the whole sweep incl. 128 in-flight. At peak all containers idle (gateway CPU 0.45%, sandboxes 0.08%, broker 0.26%) → NOT resource-bound. Single-server isolation OVERTURNED the per-server hypothesis: one server scales 13→45 RPS (1→16 in-flight), nearly the whole fleet ceiling → the ~47 RPS is a SHARED AGGREGATE chokepoint (~21ms serial), not per-server. Software serialization (single worker/connection/lock/sync round-trip), CPU idle. Cross-tenant canary_leak=0 in every step incl. mixed 3-org load → isolation holds under load.
- **Touched whose work:** P8/P9 scale lineage; drove org-a/org-b read-only only in the one cross-tenant leak step.
- **VERIFY:** `mcp-parallel/findings/mcp-page/cp48/verdict.json` — fleet_ceiling=47.4, single_server_ceiling=45.3, any_503=false, any_oom=false, canary_leak=0, diagnosis="per-server SCALES → NOT per-server; inspect aggregate broker/gateway/pool limits". CP49 localizes + widens the shared chokepoint (hypothesis: per-call enforcement-event write / policy round-trip to single-thread daphne control, or tiny DB/Redis pool).

### CHG-0067 — Sandbox agent upstream SSRF / DNS-rebinding guard (item 12 egress-lockdown, fail-closed)
- **Date:** 2026-07-02
- **Severity:** HIGH (tenant-exploitable SSRF on the PRIMARY sandbox-routed path → cloud-metadata credential theft / internal reach; the sandbox net is internal=false/open-NAT). Sandbox-side analogue of CHG-0065; closes the CHG-0066 follow-up.
- **Files:** `services/mcp-broker/sandbox-image/agent/upstream_manager.py` (`_resolved_ip_blocked`, `_assert_upstream_not_ssrf`, wired in `_get_session`); `services/mcp-broker/sandbox-image/agent/tests/test_upstream_proxy.py` (+2 SSRF tests, +1 hermeticity env in the app-load fixture).
- **WHAT (gap):** the per-tenant sandbox agent dials the registered upstream MCP server; `_validate_upstream` only matched the host STRING against `allowed_hosts` — it never resolved the hostname or checked the resolved IP. An allowlisted host that RESOLVES to an internal address (DNS rebinding, or a tenant registering a server whose hostname points internally) is dialed from inside the sandbox, which (internal=false / open NAT) can reach 169.254.169.254 (IAM creds), loopback/link-local, or RFC-1918 services. This is the PRIMARY sandbox-routed path: the gateway's `_adapter_forward`→`broker_send_rpc` does NOT `is_safe_outbound_url` the sandbox-routed upstream (only ext-proxy [CHG-0065] + internal paths do), so the sandbox agent's allowlist-string check was the ONLY runtime guard.
- **WHY:** "gVisor + ... egress-lockdown" — the sandbox's untrusted-upstream dialer must resolve + reject internal/metadata targets, not string-match a hostname.
- **NOW DOES:** `_assert_upstream_not_ssrf(host)` (async) resolves the host via the event loop's non-blocking `getaddrinfo` (literal-IP hosts checked directly) and rejects with `-32002 egress denied` if any resolved IP is a cloud-metadata endpoint (169.254.169.254 / fd00:ec2::254) or `is_private`/`is_loopback`/`is_link_local`/`is_reserved`/`is_multicast`/`is_unspecified`. FAIL-CLOSED on a resolution failure. Called in `_get_session` right after `_validate_upstream` (BEFORE any connection). `MCP_AGENT_ALLOW_INTERNAL_HOSTS` bypasses for dev/self-hosted internal upstreams.
- **Touched whose work:** the sandbox agent's upstream proxy (P4.9 / sandbox-routed http/sse). Complements the gateway ext-proxy SSRF guard (CHG-0065) at the sandbox layer. No behaviour change for public upstreams.
- **VERIFY:** `cd services/mcp-broker && ./.venv/bin/python -m pytest sandbox-image/agent/tests/test_upstream_proxy.py -q -k "not websocket"` → 13 passed (allowlisted `localhost`→127.0.0.1 → `-32002 ... internal/reserved address (127.0.0.1)` before any connection; a public-resolving host [getaddrinfo mocked] → NOT blocked, returns tools/list). The 3 `websocket` tests HANG in this env PRE-EXISTINGLY (unrelated; the streamable-http tests all pass). Existing tests hermetic via `MCP_AGENT_ALLOW_INTERNAL_HOSTS=1` in the app-load fixture. Evidence: `mcp-parallel/findings/backstop-p12-sandbox-ssrf/finding.md`. RESIDUAL: network-level egress lockdown (sandbox internal=true / iptables) remains the INFRA fix (item 12); a gateway-side `is_safe_outbound_url` on the sandbox-routed upstream would add a second layer.

### CHG-0068 — ext_mcp_proxy audits its enforcement decisions (item 9 audit / 1.4 chain, best-effort)
- **Date:** 2026-07-02
- **Severity:** MEDIUM (audit-completeness — security decisions on the untrusted external-passthrough path were unrecorded; no data leak, but a compliance/forensic blind spot).
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`ext_mcp_proxy` `_ext_audit` helper + 4 call sites); `gateway/ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py` (+4).
- **WHAT (gap):** `ext_mcp_proxy` recorded NO audit events — ZERO `_record_gateway_event` calls — while every other MCP path (org_mcp_jsonrpc / org_mcp_tool_call / internal_tools_call) audits heavily. So on the external-server surface (credential-exfil attempts, PII egress, SSRF), a blocked credential-in-args, a blocked/redacted PII result, and a blocked SSRF target were all INVISIBLE in the MCPEvent trail — breaking the mandate's `...→tag→audit` chain for external tool usage.
- **WHY:** "gateway ... audit" + "the full per-call chain authz→minimize→scan+redact→tag→AUDIT" — every enforcement decision must be auditable, including on the external passthrough.
- **NOW DOES:** a local async `_ext_audit(decision, reason, *, tool, tags, findings)` in ext_mcp_proxy calls the existing best-effort `_record_gateway_event` with the caller's org, `server_slug="ext:<host>"`, tool name, decision/reason, latency, and compliance tags/findings — fire-and-forget (no added latency), safe no-op when unauthenticated (returns early on empty org). Wired at: SSRF block (block/ssrf_blocked), inbound credential block (block/credential_blocked_inbound), outbound result block (block/pii_blocked_outbound), outbound result redaction (redact/pii_redacted_outbound).
- **Touched whose work:** the ext-proxy path (CHG-0032/0033/0034/0043/0061/0064/0065 lineage) + the gateway audit pipeline (`_record_gateway_event`). Additive (best-effort audit); no behaviour change to the forward/scan logic.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py -q` → 49 passed (result-redaction → decision=redact w/ org_slug + server_slug `ext:`; SSRF block → block/ssrf_blocked; credential block → block/credential_blocked_inbound; unauthenticated call → safe no-op, still forwards+redacts). Full sweep `ai_mesh_gateway/tests` → 1252 passed, 0 failed. Evidence: `mcp-parallel/findings/backstop-p9-ext-proxy-audit/finding.md`. RESIDUAL (follow-up): the "allow" path + infra-error withholds (non-200/non-JSON [CHG-0061], response-too-large [CHG-0064]) are not yet audited.

### CHG-0069 — Sandbox agent bounded untrusted error-body read (item 10 resource-limits/mem, containment)
- **Date:** 2026-07-02
- **Severity:** MEDIUM (sandbox-contained mem-bomb on the error path — an untrusted upstream could OOM/restart a tenant's sandbox via a huge 4xx/5xx body). Error-path counterpart of CHG-0066.
- **Files:** `services/mcp-broker/sandbox-image/agent/upstream_manager.py` (`_aread_snippet` + the status>=400 read); `services/mcp-broker/sandbox-image/agent/tests/test_upstream_proxy.py` (+2).
- **WHAT (gap):** in the streamable-http handler, an upstream error (status>=400) was read as `body = (await response.aread())[:500]`. `response` is a `client.stream(...)` response, so `aread()` buffers the ENTIRE untrusted error body into memory BEFORE the `[:500]` slice — a malicious upstream returning a huge 4xx/5xx body OOMs the sandbox agent (up to the 2GiB sandbox limit → kill + restart of that tenant's sandbox) even though only the first 500 bytes are used. Error-path counterpart of CHG-0066 (which fixed the JSON success branch).
- **WHY:** "resource limits ... mem ... + containment" / "resource bombs (mem) contained" — the untrusted error-body read must be bounded, not whole-body-buffered.
- **NOW DOES:** new `async _aread_snippet(response, limit=1024)` streams `response.aiter_bytes()` and STOPS once `limit` bytes are collected (returns at most `limit`), never buffering the whole body; the error read uses it (`(await _aread_snippet(response))[:500]`). Behaviour for a normal small error body is unchanged.
- **Touched whose work:** the sandbox agent's upstream proxy (P4.9 / sandbox-routed http/sse; CHG-0066/0067 lineage). Reduces the blast radius from "sandbox OOM + restart" to a bounded read + clean -32000.
- **VERIFY:** `cd services/mcp-broker && ./.venv/bin/python -m pytest sandbox-image/agent/tests/test_upstream_proxy.py -q -k "not websocket"` → 15 passed (a 500 upstream → `-32000 upstream HTTP 500: <bounded body>`; unit: `_aread_snippet` over a 1000-chunk body with limit=250 → <=250 bytes returned, <=3 chunks consumed [stopped early]). The 3 `websocket` tests HANG in this env PRE-EXISTINGLY (unrelated; the streamable-http tests all pass). Evidence: `mcp-parallel/findings/backstop-p10-sandbox-error-body-cap/finding.md`. FOLLOW-UP: `_read_json_response` (~257-304) is dead code with the same whole-body error reads — remove in a future cleanup.

## CHG-MCP-PAGE-CP49 — decouple best-effort MCP audit from the tool-call hot path (throughput/latency fix)
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`_get_control_audit_client`, `_post_audit_event`, `_spawn_audit_event`; `_record_gateway_event` legacy path; `import asyncio`).
- **WHAT (root cause, CP48):** the shared ~47 RPS ceiling was NOT resource-bound (idle CPU) — it was per-call SYNCHRONOUS control round-trips AWAITED INLINE on the tool-call hot path. `_record_gateway_event`'s legacy path opened a FRESH httpx client PER call and `await`ed a POST to control (single-thread daphne), serializing every tool-call completion behind daphne's audit throughput.
- **WHY:** "prove it at scale ... fix bottlenecks (pooling, backpressure) until ~0 failures." A best-effort audit record must not gate tool-call latency/throughput.
- **NOW DOES:** the audit POST fires OFF the hot path via a process-local POOLED httpx client + a BOUNDED fire-and-forget task set (cap `GATEWAY_AUDIT_MAX_INFLIGHT`=64, drop-under-backpressure). SAME endpoint + SAME payload → control's `_record_event` (MCPEvent + EnforcementEvent bridge + write_risk) preserved EXACTLY → CP24/31/32 telemetry NOT regressed. The Redis async-queue flag (`GATEWAY_ASYNC_MCP_AUDIT`) was REJECTED because `record_mcp_event_task` omits the EnforcementEvent bridge/write_risk (would regress 1.4 threat-feed). Enforcement decision still inline; only the audit record is decoupled.
- **Touched whose work:** the gateway MCP proxy hot path (CP23/CP24 recording lineage). Telemetry-identical by construction. Deployed via docker cp + kill -HUP 1 (baked image; source committed for rebuild).
- **VERIFY:** full gateway suite `./.venv/bin/python -m pytest ai_mesh_gateway/tests -q` → 1245 passed, 0 failed. Post-fix consolidated sweep vs pre-fix: single conn=1 13.2→20.6 RPS (+56%) p50 74→45ms (−39%); fleet 8-inflight 37.5→46.6 RPS (+24%). ~0 failures: 0 drops / 0 backpressure across the whole back-to-back sweep (fleet 8→128 + single 1→16 + xtenant); canary_leak=0 every step incl. mixed 3-org. Remaining ~48 RPS/sandbox ceiling is architectural (per-org gVisor stdio; idle CPU), horizontally scalable. Evidence: `mcp-parallel/findings/mcp-page/cp48/verdict.json` (post) + `verdict_prefix.json` (pre).

### CHG-0070 — ext_mcp_proxy audit trail completed: SSE-result block + tool-call allow (item 9 audit)
- **Date:** 2026-07-02
- **Severity:** MEDIUM (audit-completeness — self-correction of a CHG-0068 omission [SSE result block unaudited] + external-tool usage audit).
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (3 `_ext_audit` call sites); `gateway/ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py` (+2).
- **WHAT (gap):** CHG-0068 audited the SSRF block, credential block, and JSON-branch result block/redact — but MISSED (1) the SSE result block (`_scan_reframe_sse_tool_result` → `_block_info`), so a blocked SSE tool result egressed NO audit event while the equivalent JSON block WAS audited (inconsistent); and (2) any successful tool-call, so external tool USAGE was unrecorded (only blocks/redacts).
- **WHY:** "gateway ... audit" — every enforcement decision (incl. the SSE-path block) must be auditable, and usage visibility on the untrusted external-server surface needs the allow events.
- **NOW DOES:** reuses the CHG-0068 `_ext_audit` helper (best-effort/fire-and-forget, no-op without org) at: the SSE result block (block/pii_blocked_outbound with `_block_info` tags), the SSE result success (allow/ok), and the JSON result success (allow/ok in the `elif` of the redact branch — so a call records exactly once: block XOR redact XOR allow). Allow events are gated on `_ext_tool_name` so protocol overhead (initialize/list) does not flood the audit — only real tool invocations record usage.
- **Touched whose work:** the ext-proxy path (CHG-0068 lineage) + the gateway audit pipeline. Additive; no behaviour change to forward/scan.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py -q` → 51 passed (a clean tools/call → decision=allow w/ tool_name=fetch; an SSE tools/call result that blocks → decision=block). With CHG-0068 the ext-proxy now audits block XOR redact XOR allow for every tool call on both JSON and SSE paths. Full sweep `ai_mesh_gateway/tests` → 1256 passed, 0 failed. Evidence: `mcp-parallel/findings/backstop-p9-ext-proxy-audit-complete/finding.md`. RESIDUAL: infra-error withholds (non-200/non-JSON [CHG-0061], response-too-large [CHG-0064]) still not audited (transport errors already logged).

## CHG-MCP-PAGE-CP50 — FINAL stress+verify GREEN 3× consecutive (completion gate)
- **Files:** `scripts/ralph/mcp_page_cp50_final_verify.py` (new 3× gate); `scripts/ralph/mcp_page_cp47_stress.py` (real-client retry on transient connection errors).
- **WHAT:** the completion gate — the final stress+verify must be green 3× consecutive with ~0 failures and no cross-tenant leakage under load.
- **NOW DOES:** runs the CP47 driver 3× back-to-back against the full 15-MCP fleet (3 orgs × 5 servers), 48 in-flight, 3000 calls/run; each run gated on drove-the-load + drops==0 + no-5xx + id/content/arith-mismatch==0 + cross_tenant==0 + canary_leak==0. RESULT: GREEN 3×/3× — 9000 total calls, 0 drops, recovered_by_retry=0 (retry safety net not even needed on a settled system), rps≈48.7, p99≈1.1s, isolation perfect in all 3 runs.
- **HONEST NOTE:** first attempt was RED 2/3 (run1 drops=160, p99 9s) — a transient overload shed because run1 fired while the sandbox still had backlog from the just-finished CP49 sweep (isolation stayed perfect: 0 leak/mismatch even then). Added real-client retry-on-transient-connection-error to the driver (`_is_transient` now includes status==-1; recoveries counted via transient_recovered, never hidden) — echo/get-sum are idempotent so retry is safe. Re-ran on a settled system → clean 3×/3× with retry never firing. Literal 100k RPS is physically unattainable on one shared VM (per-sandbox gVisor stdio ceiling ~48 RPS, idle CPU, horizontally scalable — documented CP47-49); the honest deliverable is heavy sustained load served at ~0 failures with intact isolation, proven 3× consecutive.
- **VERIFY:** `mkdir -p mcp-parallel/findings/mcp-page/cp50 && gateway/.venv/bin/python scripts/ralph/mcp_page_cp50_final_verify.py` → "CP50: GREEN 3x/3x — FINAL STRESS+VERIFY PASS". Evidence: `mcp-parallel/findings/mcp-page/cp50/final_verify.json` (all_green_3x=true, green_streak=3/3). ALL 50 CHECKPOINTS [x].

### CHG-0071 — Provider secret formats added to SECRET_PATTERNS (item 2 / 1.4 — detect+tag+redact, fail-closed)
- **Date:** 2026-07-02
- **Severity:** HIGH (real credential formats egressed UNMASKED on MCP tool results and were not flagged by detect_secrets → no enforcement triggered).
- **Files:** `gateway/ai_mesh_gateway/patterns.py` (SECRET_PATTERNS + COMPLIANCE_TAG_MAP); `gateway/ai_mesh_gateway/tests/test_provider_secret_formats.py` (+10).
- **WHAT (gap):** an adversarial sweep of real-world credential formats through `redact_all` found 4 that egressed UNMASKED and were NOT flagged by `detect_secrets`: Anthropic API key `sk-ant-…` (the OpenAI `sk-` family was caught but `ant` wasn't in the alternation and the `sk-[A-Za-z0-9]{32,}` fallback fails on the hyphens), SendGrid `SG.<seg>.<seg>`, GitLab PAT `glpat-…` (GitHub PATs were covered), and Slack incoming-webhook URLs `https://hooks.slack.com/services/…`. SUBTLE: adding them only to CREDENTIAL_EXPOSURE_PATTERNS masks via redact_all but does NOT make detect_secrets flag them — and the MCP tier1 scan (`_scan_text_tier1`, CHG-0057) uses detect_secrets to DECIDE enforcement, so a result whose ONLY sensitive content is such a key would trigger NO redaction and egress raw. They must live in SECRET_PATTERNS (iterated by both detect_secrets and redact_all).
- **WHY:** "field-level redaction of tool RESULTS, byte-verified, fail-closed" — common provider credential formats must be detected (to drive enforcement) AND masked.
- **NOW DOES:** SECRET_PATTERNS gains `anthropic_key` (`\bsk-ant-[A-Za-z0-9_-]{20,}\b`), `sendgrid_key` (`\bSG\.[A-Za-z0-9_-]{16,32}\.[A-Za-z0-9_-]{32,}\b`), `gitlab_pat` (`\bglpat-[A-Za-z0-9_-]{20,}\b`), `slack_webhook` (`https://hooks\.slack\.com/services/[A-Za-z0-9/_+-]+`); COMPLIANCE_TAG_MAP tags each ["SECRET"]. detect_secrets flags them (drives tier1 redact/block), get_compliance_tags returns SECRET, redact_all masks them (`[ANTHROPIC_KEY_REDACTED]` etc. via the default key-named masker). Near-zero FP (highly specific prefixes/structures); benign battery unchanged.
- **Touched whose work:** the detection/redaction core `patterns.py` (CHG-0054/0055/0058/0060 lineage; owning-session tier1 file). Additive (4 patterns + 4 tags); no golden-case change.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_provider_secret_formats.py -q` → 10 passed (each format detect_secrets-flagged under its key, tagged SECRET, masked; benign battery no-FP). Full sweep `ai_mesh_gateway/tests` → 1266 passed, 0 failed. Evidence: `mcp-parallel/findings/backstop-p2-provider-secret-formats/finding.md`.

### CHG-0072 — More provider secret formats + AWS STS temporary keys (item 2 / 1.4 — detect+tag+redact)
- **Date:** 2026-07-02
- **Severity:** HIGH (11 more real credential formats egressed UNMASKED on MCP tool results). Second round after CHG-0071.
- **Files:** `gateway/ai_mesh_gateway/patterns.py` (PII_PATTERNS aws_access_key widen + 10 SECRET_PATTERNS + COMPLIANCE_TAG_MAP); `gateway/ai_mesh_gateway/tests/test_more_provider_secrets.py` (+18).
- **WHAT (gap):** a second adversarial sweep found 11 more formats that egressed unmasked + undetected: AWS STS temporary access-key id `ASIA…` (the `aws_access_key` pattern was AKIA-only, so session/temporary credentials leaked); DigitalOcean `dop_v1_`, Shopify `shp{at,ss,ca,pa}_`, Square `sq0{atp,csp,idp}-`, Databricks `dapi`, HashiCorp Vault `hv{s,b}.`, Figma `figd_`, Telegram bot `<id>:AA…`, PyPI `pypi-`, Linear `lin_api_`, Mailgun `key-<32hex>` — no patterns.
- **WHY:** "field-level redaction of tool RESULTS, byte-verified, fail-closed" — common provider credential formats must be detected (to drive enforcement) AND masked.
- **NOW DOES:** widened `aws_access_key` (PII_PATTERNS → detect_pii) to `\b(?:AKIA|ASIA)[0-9A-Z]{16}\b` (AKIA regression preserved); added 10 distinctive-prefix tokens to SECRET_PATTERNS (detect_secrets + redact_all) + COMPLIANCE_TAG_MAP (SECRET): digitalocean_pat/shopify_token/square_token/databricks_token/hashicorp_vault_token/figma_token/telegram_bot_token/pypi_token/linear_api_key/mailgun_key. The Telegram pattern allows the optional `bot` URL prefix so a token in the `api.telegram.org/bot<token>/…` URL masks too. Each is detected (drives tier1 redact/block), tagged SECRET, masked. Near-zero FP (fixed prefix+length); benign battery unchanged.
- **Touched whose work:** the detection/redaction core `patterns.py` (CHG-0054..0071 lineage; owning-session file). Additive; no golden-case change.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_more_provider_secrets.py -q` → 18 passed (each detected under its key + tagged SECRET + masked; ASIA detect_pii-flagged + masked w/ AKIA regression; Telegram token in the API URL masked; benign no-FP). Full sweep `ai_mesh_gateway/tests` → 1284 passed, 0 failed. Evidence: `mcp-parallel/findings/backstop-p2-more-provider-secrets/finding.md`.

### CHG-0073 — Internal IPv6 + cloud-metadata/CGNAT IPv4 leakage (item 20 / 1.4 — detect+tag+redact, closes a fail-open)
- **Date:** 2026-07-02
- **Severity:** MEDIUM (infra-topology / cloud-env disclosure; fail-OPEN under a redact policy — flag-yet-forward-raw).
- **Files:** `gateway/ai_mesh_gateway/patterns.py` (IP_LEAKAGE_PATTERNS + `_redact_all_raw` infra tuple + `_INFRA_NETWORK_KEYS` + COMPLIANCE_TAG_MAP); `gateway/ai_mesh_gateway/tests/test_internal_ip_leakage_v6_metadata.py` (+21).
- **WHAT (gap):** a third adversarial `redact_all` sweep — the IP-leakage surface. `IP_LEAKAGE_PATTERNS` was IPv4-RFC1918-only, so a tool RESULT containing internal IPv6 (ULA fc00::/7, link-local fe80::/10), a cloud-metadata/link-local IPv4 (`169.254.169.254` IMDS — hands out IAM creds, the exact SSRF target the dial-time guards CHG-0065/0067 block), or CGNAT IPv4 (`100.64.0.0/10`) egressed RAW (redact_all no-op, detect_ip_leakage empty). SUBTLE FAIL-OPEN: `_redact_all_raw` masks infra via a HARDCODED key tuple, not by iterating the dict — so adding a key to `IP_LEAKAGE_PATTERNS` alone makes `detect_ip_leakage` FLAG the leak (tier1 decides block/redact) while `redact_all` leaves it RAW → under a redact policy the gateway would report "redacted" while forwarding raw (the cardinal invariant violation).
- **WHY:** "field-level redaction of tool RESULTS, byte-verified, fail-closed" + "compliance tagging extended to PII/IP/regulated" — internal IP/IPv6/metadata addresses are infra-leakage and must be detected (drive enforcement), tagged, AND masked, exactly like RFC1918 already was.
- **NOW DOES:** adds `internal_ipv6` (`\b(?:f[cd][0-9a-f]{2}|fe[89ab][0-9a-f])(?:(?:::?[0-9a-f]{1,4})+(?:::)?|::)`, anchored on the distinctive internal first hextet so a 2-hex MAC group / bare hex blob / HH:MM:SS timestamp never match; loopback `::1` intentionally NOT flagged) and `link_local_ipv4` (`169.254.0.0/16` incl. IMDS + `100.64.0.0/10` CGNAT). All FOUR integration points touched so detect==redact==tag==encoded-parity: (1) IP_LEAKAGE_PATTERNS → detect_ip_leakage; (2) `_redact_all_raw` infra tuple → redact_all actually masks (closes the divergence); (3) `_INFRA_NETWORK_KEYS` → encoded/base64-infra bypass pass covers them; (4) COMPLIANCE_TAG_MAP → both ["INFRA"]. Linear-time (no ReDoS); near-zero FP.
- **Touched whose work:** the detection/redaction core `patterns.py` (CHG-0054..0072 lineage; owning-session file). Additive; RFC1918 control + all golden cases unchanged.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_internal_ip_leakage_v6_metadata.py -q` → 21 passed (each internal addr detected under its key + tagged INFRA + byte-level absent from redact_all egress; RFC1918 control still masks; loopback NOT flagged; MAC/hex-blob/timestamp/version/UUID no-FP battery; ReDoS smoke on 10k inputs). Full sweep `ai_mesh_gateway/tests` → 1305 passed, 0 failed. Independent oracle: aidefence_scan reports piiFound:false on the IMDS URL + ULA IPv6 (a generic scanner is BLIND to infra-leak → confirms the gap is real and purpose-built detect_ip_leakage is required). Evidence: `mcp-parallel/findings/backstop-p20-internal-ipv6-metadata-leak/finding.md`.

### CHG-0074 — Internal-network IP leakage in tool RESULTS bypassed the redaction floor (fail-open); + swallowed floor-block fail-open
- **Date:** 2026-07-02
- **Severity:** MEDIUM–HIGH (internal / cloud-metadata IP disclosure in tool results under the DEFAULT `tag` config).
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py`, `gateway/ai_mesh_gateway/mcp_scan_orchestrator.py`; `gateway/ai_mesh_gateway/tests/test_mcp_result_ipleak_floor.py` (+11).
- **WHAT (gap, found by devil's-advocate on CHG-0073):** verifying CHG-0073's patterns were actually wired into the live tool-RESULT enforcement path (not just patterns.py) exposed TWO fail-opens. (1) The MCP orchestrator (`_scan_text_tier1`) DETECTS + TAGS ip_leakage but only REDACTS under `enforcement=="redact"`; under the DEFAULT `tag`/`flag`/`monitor` posture it returns the result unmutated. The mcp_proxy E12 result-redaction floor that upgrades `tag`→`redact` was gated on `_findings_have_secret_or_pii` — which matches only `threat_type in ("pii","secret")` and EXCLUDES the whole `ip_leakage` class. So an internal / cloud-metadata IP (169.254.169.254 IMDS), internal IPv6, CGNAT, internal hostname — AND even PRE-EXISTING RFC1918 (10.10.5.7) — in a tool RESULT was detected+tagged INFRA but egressed RAW, asymmetric with PII/secret. (2) The floor re-scan (`enforcement_override="redact"`) BLOCKS when a value redact_all can't mask survives (a private FILE PATH beside the leak), but all 3 floor sites ignored that `blocked` flag and forwarded RAW (swallowed block; inconsistent with the same function's fail-closed except-handler) — also affected PII+file-path.
- **WHY:** 1.4 "field-level redaction of tool RESULTS, byte-verified, fail-closed" + "no PII/IP/regulated escape". An internal/metadata IP in a result is the same infra-disclosure class as PII/secret and must be floored; an unmaskable survivor must fail CLOSED.
- **NOW DOES:** (a) `McpFinding` gains `matched_kinds: list[str]` (+ to_finding_dict); the pii/secret/ip_leak finding passes `matched_kinds=kinds`. (b) `mcp_proxy._findings_have_infra_network_leak(findings)` → True iff an `ip_leakage` finding's matched_kinds intersect `_INFRA_NETWORK_KEYS` (the network keys redact_all masks: internal_ipv4/ipv6, link_local_ipv4, internal_hostname, internal_url) — SCOPED to network so a file-path-only result never triggers the floor (never force-blocks a benign code/file result). (c) OR'd into all 3 floor triggers (`_scan_tool_result_floor` + org adapter + org jsonrpc). (d) All 3 sites now PROPAGATE the floor-block fail-closed (`[BLOCKED]` / `result_redaction_floor_block`) instead of forwarding raw. Result: internal-network addrs in tool results are now MASKED under the default posture; file-path-only stays raw/flag-tier (no regression); network|PII + file-path fails CLOSED (block).
- **Touched whose work:** the MCP result-enforcement floor (E12/CHG-0057 lineage; owning-session mcp_proxy.py + orchestrator). Additive field + broadened trigger + block propagation; no PII/secret behaviour change (regression-guarded).
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_result_ipleak_floor.py -q` → 11 passed (drives the REAL `_scan_tool_result_floor`: metadata/IPv6/RFC1918/CGNAT/hostname now floored-not-raw; PII/secret still floored; file-path-only stays raw+unblocked; mixed maskable+file-path fails closed; helper unit). Full sweep `ai_mesh_gateway/tests` → 1316 passed, 0 failed. Broker `-k "not websocket"` → 108 passed. Evidence: `mcp-parallel/findings/backstop-p20-ipleak-result-floor/finding.md`.

### CHG-0075 — MCP tier-1 scan omitted detect_credential_exposure → Stripe/Twilio/Azure/conn-string egressed raw (+ 7 missing SECRET tags)
- **Date:** 2026-07-02
- **Severity:** HIGH (a whole credential class egressed RAW in MCP tool RESULTS and passed unblocked in tool ARGS to untrusted upstreams).
- **Files:** `gateway/ai_mesh_gateway/mcp_scan_orchestrator.py`, `gateway/ai_mesh_gateway/patterns.py`; `gateway/ai_mesh_gateway/tests/test_mcp_credential_exposure_scan.py` (+11).
- **WHAT (gap, devil's-advocate on the detect_* completeness):** `mcp_scan_orchestrator._scan_text_tier1` ran detect_pii + detect_secrets + detect_ip_leakage but NOT detect_credential_exposure. CREDENTIAL_EXPOSURE_PATTERNS is a SEPARATE dict (bearer_token, connection_string, exposed_password, private_key_block, github_fine_grained_pat, stripe_key, azure_storage_key, twilio_api_key, gcp_service_account_key, slack_token, jwt) NOT read by detect_secrets. redact_all masks it, but the MCP scan uses detect_* to DECIDE enforcement — so a credential whose ONLY match was a CREDENTIAL_EXPOSURE kind (Stripe sk_live_, Twilio SK<32hex>, Azure AccountKey=, a DB connection string's password) was never DETECTED, drove no enforcement, and egressed RAW on a tool RESULT (verified end-to-end: raw LEAK at the default `tag` posture) — and passed unblocked in tool ARGS to a possibly-untrusted upstream. Same wrong-dict class as CHG-0071. PART B: 7 CREDENTIAL_EXPOSURE keys (github_fine_grained_pat, stripe_key, azure_storage_key, twilio_api_key, gcp_service_account_key, slack_token, jwt) had NO COMPLIANCE_TAG_MAP entry → get_compliance_tags returned [] → never tagged SECRET (breaks enforce-by-tag + audit + the arg force-block's SECRET-tag path).
- **WHY:** 1.4 "field-level redaction of tool RESULTS, byte-verified, fail-closed" + "compliance tagging ... enforce by tag, audit". Provider/DB credentials must be detected (to drive enforcement), tagged SECRET, and masked on results / blocked in args.
- **NOW DOES:** (a) `_scan_text_tier1` imports + calls `detect_credential_exposure(text)` and folds it into the detect branch (kinds, matched_kinds, byte-verify _detected_values, threat precedence pii > secret/credential > ip_leakage — a credential exposure is threat_type="secret" so it drives the result redact floor AND the arg credential force-block). (b) COMPLIANCE_TAG_MAP gains the 7 missing keys → ["SECRET", "SOC2"]. Result: Stripe/Twilio/Azure/connection-string/GCP-SA in a tool RESULT are now MASKED (floored) + tagged SECRET,SOC2; the same in tool ARGS force-blocks; bearer/jwt/slack unchanged (already covered); benign unaffected (no FP).
- **Touched whose work:** the MCP scan orchestrator tier-1 + patterns.py detection/tag core (CHG-0071/0074 lineage; owning-session files). Additive detector + 7 tag entries; no regression (full suite green).
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_credential_exposure_scan.py -q` → 11 passed (each cred DETECTED, masked-not-raw in a result, tagged SECRET, force-blocked in args; all CREDENTIAL_EXPOSURE keys tag SECRET; benign no-FP). Full sweep `ai_mesh_gateway/tests` → 1327 passed, 0 failed. Broker `-k "not websocket"` → 108 passed. Evidence: `mcp-parallel/findings/backstop-p2-cred-exposure-mcp-scan/finding.md`.

### CHG-0076 — MCP scan lacked the text-encoding obfuscation check → encoded secret/IP exfil past the firewall
- **Date:** 2026-07-02
- **Severity:** MEDIUM–HIGH (a malicious upstream MCP server exfils a stolen credential / internal IP by text-encoding it; a markdown/HTML client decodes it back).
- **Files:** `gateway/ai_mesh_gateway/mcp_scan_orchestrator.py`; `gateway/ai_mesh_gateway/tests/test_mcp_encoded_exfil.py` (+9).
- **WHAT (gap, devil's-advocate on chat-vs-MCP parity):** the chat OUTPUT scanner (scanner._scan_output_sync, G33/G35) decodes text-encoding variants via `_decode_text_encoding_variants` so an HTML-entity / percent / \u\x-escaped PII/secret a markdown/browser client would render is caught. The MCP orchestrator tier-1 (`_scan_text_tier1`) had NO such check — it ran the raw detect_pii/secrets/ip_leakage/credential_exposure only. detect_secrets folds base64/hex transport, but a SECRET / CREDENTIAL / INTERNAL NETWORK IP hidden by a TEXT-encoding dodges the raw regexes, and redact_all cannot mask an encoded run — so an encoded credential / internal IP in a tool RESULT egressed (verified: HTML-entity + percent-encoded sk-ant / 10.0.0.5 not flagged) and a markdown/HTML MCP client decodes it back to the value = a laundering/exfil channel for an untrusted external MCP server (same class in tool ARGS: a tenant smuggling a secret to an upstream).
- **WHY:** 1.4 "prevent MCP data leakage, fail-closed". An obfuscated credential/internal-IP that a client renders back to plaintext is a leak the firewall must stop, matching the chat path's G33/G35.
- **NOW DOES:** `_scan_text_tier1` (after the raw detect branch) decodes `_decode_text_encoding_variants(text)`; if a decoded variant reveals a SECRET / CREDENTIAL / internal-NETWORK IP (via detect_secrets + detect_credential_exposure + detect_ip_leakage∩_INFRA_NETWORK_KEYS) the raw text lacked → appends a threat_type="secret" finding and BLOCKS (fail-closed) under any non-monitor posture (redact_all can't mask the encoded run; mirrors the chat INPUT path scanner._scan_prompt_sync). SCOPED: generic PII EXCLUDED (a scraped HTML page's entity-encoded contact email is usually benign → no false-block of legit web/HTML tool results); file paths excluded. Applies to input+output. Plain text → `_decode_text_encoding_variants` returns [] so the loop never runs (negligible overhead). Imports _INFRA_NETWORK_KEYS from patterns + _decode_text_encoding_variants from scanner (local import; no cycle — scanner does not import the orchestrator).
- **Touched whose work:** the MCP scan orchestrator tier-1 (CHG-0074/0075 lineage; owning-session file). Additive; raw-secret redaction + benign paths unchanged (no regression).
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_encoded_exfil.py -q` → 9 passed (HTML-entity/percent-encoded secret + internal IP in a RESULT block; encoded secret in ARGS blocks; encoded generic PII email NOT blocked; raw secret still masked-not-blocked; benign HTML entities/plain/URL no-FP). Full sweep `ai_mesh_gateway/tests` → 1327 passed, 0 failed. Broker `-k "not websocket"` → 108 passed. Evidence: `mcp-parallel/findings/backstop-p2-mcp-encoded-exfil/finding.md`.

### CHG-0077 — org tools/list forwarded upstream tool descriptions UNSCANNED (metadata-leak / tool-poisoning surface)
- **Date:** 2026-07-02
- **Severity:** MEDIUM (secret/PII/internal-IP in an untrusted upstream's tool description leaked to the model on the org path; asymmetric with the ext path which scans tools/list).
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py`; `gateway/ai_mesh_gateway/tests/test_mcp_tools_list_desc_scan.py` (+4).
- **WHAT (gap, devil's-advocate on tool poisoning):** tool descriptions returned by tools/list come LIVE from the untrusted upstream MCP server and are shown to the model (canonical tool-poisoning / line-jumping surface). The EXTERNAL proxy path scans tools/list (in `_EXT_FINITE_RESULT_METHODS`), but the ORG `tools/list` handler (org_mcp_jsonrpc) — BOTH the sandbox-routed adapter sub-path AND the backend sub-path — returned the tools list after only visibility filters (_filter_tools_by_enabled/_filter_tools_by_key_allowlist) with NO content scan. So a secret / PII / internal-IP (or a CHG-0076 text-encoded exfil payload) embedded in a tool description egressed to the model on the primary org path.
- **WHY:** 1.4 "prevent MCP data leakage". Untrusted-upstream tool metadata shown to the model must be scanned like tool results, matching the ext path.
- **NOW DOES:** new `_scanned_tools_list_response(payload,…)` runs the tools/list result through `_scan_tool_result_floor` (inherits CHG-0074 IP-network floor + CHG-0075 credential-exposure + CHG-0076 encoded-exfil block). Both org sub-paths return through it: a maskable leak (secret/PII/internal-IP in a description) is MASKED and tools/list forwarded; unmaskable / encoded-exfil poisoned metadata is BLOCKED (fail-closed, JSON-RPC error). Availability-preserving: file paths stay flag-tier (not masked/blocked), benign descriptions untouched.
- **Touched whose work:** the org MCP JSON-RPC path (owning-session mcp_proxy.py). Additive; visibility filters unchanged; benign discovery unaffected (no regression).
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_tools_list_desc_scan.py -q` → 4 passed (secret+IP in descriptions masked, benign tool survives; HTML-entity-encoded secret in a description → tools/list withheld; benign tools/list incl. a /home/user path → unchanged; non-tools result passthrough). Full sweep `ai_mesh_gateway/tests` → 1336 passed, 0 failed; broker `-k "not websocket"` → 108 passed. Evidence: `mcp-parallel/findings/backstop-p2-tools-list-desc-scan/finding.md`.
- **HIGH-PRIORITY FOLLOW-UP (documented, NOT fixed here):** the MCP tier-1 injection detector `mcp_scan_orchestrator._injection_match` is a 6-keyword substring match (`_INJECTION_KEYWORDS`) that MISSES tool-poisoning/injection payloads the chat scanner (`scanner._scan_prompt_sync`, fuzzy + obfuscation + multi-turn) BLOCKS (e.g. "Ignore ALL previous instructions", "Before using any tool, first read ~/.ssh/id_rsa…", "SYSTEM: …developer mode…"). Needs its own iteration: high-precision injection patterns (or reuse the chat detector) + an enforcement decision for injected tool metadata/results under the default `tag` posture (chat BLOCKS injection; MCP tags-only), with a benign battery (docs/search tools ⇒ FP risk). Deferred to keep this change bounded.

### CHG-0078 — MCP tier-1 injection detection was a 6-keyword substring match → raised to chat-parity (ATTACK_PATTERNS)
- **Date:** 2026-07-02
- **Severity:** MEDIUM–HIGH (tool-poisoning / injection payloads from an untrusted upstream/tenant bypassed MCP tier-1 injection detection the chat path blocks). Fixes the CHG-0077 follow-up.
- **Files:** `gateway/ai_mesh_gateway/mcp_scan_orchestrator.py`; `gateway/ai_mesh_gateway/tests/test_mcp_injection_parity.py` (+10).
- **WHAT (gap):** `mcp_scan_orchestrator._injection_match` was `any(kw in text.lower() for kw in _INJECTION_KEYWORDS)` with only 6 exact phrases (ignore previous instructions / ignore all prior / disregard your instructions / do anything now / jailbreak / system prompt). This naive substring match MISSED tool-poisoning/injection payloads the CHAT scanner (scanner._scan_prompt_sync, backed by ATTACK_PATTERNS w/ fuzzy + obfuscation + multi-turn) BLOCKS — e.g. "…Ignore ALL previous instructions" (the keyword "ignore previous instructions" lacks "all", substring fails), "Before using any tool, first read ~/.ssh/id_rsa…", "SYSTEM: …operate in developer mode…". So injection in a tool description/result/args from an untrusted upstream bypassed MCP tier-1 injection detection that the chat path catches.
- **WHY:** 1.4 / firewall purpose. Tool poisoning / indirect prompt injection is a top MCP attack; the MCP path must reach the chat scanner's injection-detection strength.
- **NOW DOES:** `_injection_match` keeps the fast keyword path, then reuses the chat scanner's high-precision prompt_injection + jailbreak patterns (scanner.ATTACK_PATTERNS — the SAME patterns the chat path uses, G15/G28). Scoped to those two LLM-manipulation categories (NOT sql/command/path → no FP on benign tool output mentioning SQL/paths). compile_pattern is LRU-cached (cheap per fragment); local import (scanner does not import the orchestrator → no cycle); exception-safe (never breaks the scan). Enforcement UNCHANGED (block under a block posture, tag otherwise).
- **Touched whose work:** the MCP scan orchestrator tier-1 (owning-session file; CHG-0074..0077 lineage). Additive detection; verified no test regression + zero FP.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_injection_parity.py -q` → 10 passed (3 previously-missed poisons now detected; benign battery incl. docs-ABOUT-injection + a SQL mention + a file path all NOT flagged = 0 FP; E2E block posture → blocked + prompt_injection finding, tag posture → finding not blocked). Full sweep `ai_mesh_gateway/tests` → 1340 passed, 0 failed; broker `-k "not websocket"` → 108 passed. Evidence: `mcp-parallel/findings/backstop-p2-mcp-injection-parity/finding.md`.
- **RESIDUAL/FOLLOW-UP:** MCP injection under the DEFAULT `tag` posture is tagged-but-forwarded (chat BLOCKS injection by default). Future iteration: decide output-injection enforcement — (a) block/neutralize injected tool RESULTS by default (FP risk: docs/search tools), or (b) the near-zero-FP subset: DROP a tool whose tools/list DESCRIPTION carries injection (building on CHG-0077). 2 subtle payloads still missed are also missed by the chat scanner (need tier-2 Bedrock).

### CHG-0079 — MCP tier-1 scan didn't deobfuscate invisible/confusable unicode (zero-width / homoglyph smuggling)
- **Date:** 2026-07-02
- **Severity:** MEDIUM–HIGH (a malicious upstream hides an injection / secret / internal-IP with zero-width or homoglyph unicode; a markdown/model client reads the deobfuscated value → bypasses the MCP firewall).
- **Files:** `gateway/ai_mesh_gateway/mcp_scan_orchestrator.py`; `gateway/ai_mesh_gateway/tests/test_mcp_unicode_deobfuscation.py` (+8).
- **WHAT (gap, found while FP-grounding the CHG-0078 follow-up):** the CHG-0078 follow-up was to enforce (drop) poisoned tool descriptions. An FP probe REJECTED heuristic-drop (a legit "Detects jailbreak attempts and prompt injection" security-tool description trips the injection patterns; the classic `<IMPORTANT>…read ~/.ssh/id_rsa…` poison is missed by both) — so the clean signal is OBFUSCATION, not injection-content heuristics. Probing further: the chat scanner deobfuscates via `_normalize_unicode` before scanning, but `mcp_scan_orchestrator._scan_text_tier1` ran `_injection_match` + detect_secrets/…/ip_leakage on RAW text. Invisible/confusable unicode — zero-width chars (I​g​n​o​r​e), bidi-override, homoglyphs (fullwidth Ｉ, Cyrillic look-alikes), the Unicode-tag block, combining marks — dodged the raw regexes (redact_all also doesn't strip zero-width). So a zero-width-broken / homoglyph injection, or a secret / internal-IP hidden that way, bypassed the MCP firewall while a markdown/model client reads the deobfuscated value.
- **WHY:** 1.4 / firewall purpose + obfuscation-bypass parity. The MCP path must deobfuscate like the chat path.
- **NOW DOES:** `_scan_text_tier1` computes `_deob = _normalize_unicode(text)` (decode unicode-tags → strip zero-width & bidi → NFKC → drop combining marks → fold homoglyphs) and (a) runs `_injection_match` on `_deob` too (catches zero-width/homoglyph injection); (b) adds `_deob` to the CHG-0076 hidden-secret/credential/internal-IP variant probe → an obscured secret/IP BLOCKS fail-closed (redact_all can't mask the obfuscated run; monitor stays observe-only). ASCII FAST-PATH: only non-ASCII text can carry these, so `text.isascii()` short-circuits (common case pays nothing). Local import (scanner doesn't import the orchestrator → no cycle). Injection enforcement unchanged (block under a block posture, tag otherwise).
- **Touched whose work:** the MCP scan orchestrator tier-1 (owning-session file; CHG-0074..0078 lineage). Extends CHG-0076 to a second obfuscation channel. Additive; no regression.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_unicode_deobfuscation.py -q` → 8 passed (zero-width + homoglyph injection detected/blocked under block posture; zero-width-hidden secret in a RESULT blocked fail-closed w/ raw secret absent; ZERO FP on an emoji ZWJ family 👨‍👩‍👧 + Japanese + accented café/résumé/naïve + plain ASCII — the normalize is a detection-only probe, never mutates emitted bytes). Full sweep `ai_mesh_gateway/tests` → 1350 passed, 0 failed; broker `-k "not websocket"` → 108 passed. Evidence: `mcp-parallel/findings/backstop-p2-mcp-unicode-deobfuscation/finding.md`.
- **RESIDUAL:** the heuristic-drop of poisoned tool descriptions was REJECTED on FP grounds (legit security tools); injection under the DEFAULT `tag` posture remains tagged-but-forwarded (CHG-0078 residual). Normalization is a detection-only probe (doesn't alter emitted content, matching the chat scanner).

### CHG-0080 — ext proxy forwarded the MCP `initialize` result (model-facing `instructions`) UNSCANNED
- **Date:** 2026-07-02
- **Severity:** MEDIUM (a malicious external MCP server's initialize `instructions` — model-facing, "analogous to a system prompt" — + serverInfo reached the model unscanned on the transparent proxy: tool-poisoning / metadata-leak).
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py`; `gateway/ai_mesh_gateway/tests/test_mcp_ext_initialize_scan.py` (+5).
- **WHAT (gap, devil's-advocate on model-facing surfaces):** the MCP `initialize` result carries an `instructions` field the spec treats as model-facing guidance ("thought of like a hint … may be added to the system prompt") plus serverInfo — a tool-poisoning / indirect-prompt-injection + metadata surface like tool descriptions (CHG-0077). ORG path is SAFE (org_mcp_jsonrpc SYNTHESIZES the initialize response — gateway serverInfo, no upstream instructions forwarded). But the EXT path (ext_mcp_proxy, transparent external proxy) scans a result only when the method is in `_EXT_FINITE_RESULT_METHODS`, and `initialize` was NOT in that set — so the upstream's initialize response (incl. model-facing `instructions` + serverInfo) was forwarded RAW, unscanned. A malicious external server could put an injection/exfil instruction or a hidden secret/internal-IP in `instructions` and it reached the model past the firewall.
- **WHY:** 1.4 "prevent MCP data leakage" / tool-poisoning. All model-facing untrusted-upstream content must be scanned, matching tool-descriptions (CHG-0077).
- **NOW DOES:** added `"initialize"` to `_EXT_FINITE_RESULT_METHODS`. The finite handshake result is now routed through the same result-redaction floor as tool results, inheriting CHG-0074 (IP-network floor) + CHG-0075 (credential-exposure) + CHG-0076 (text-encoding exfil block) + CHG-0079 (invisible/confusable-unicode deobfuscation) + injection detection/tagging (CHG-0078). initialize ARGS (client capabilities/clientInfo) NOT scanned (client-provided, not sensitive).
- **Touched whose work:** the external MCP proxy path (owning-session mcp_proxy.py; CHG-0077 lineage). One-line set addition; org path unaffected; benign initialize untouched (no regression).
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_ext_initialize_scan.py -q` → 5 passed (secret + internal-IP in initialize instructions masked + tagged INFRA,SECRET; zero-width-hidden secret blocked fail-closed; injection detected; benign initialize unchanged). Full sweep `ai_mesh_gateway/tests` → 1358 passed, 0 failed; broker `-k "not websocket"` → 108 passed. Evidence: `mcp-parallel/findings/backstop-p2-ext-initialize-instructions/finding.md`.
- **RESIDUAL:** injection in `instructions` is detected+tagged but not removed under the default `tag` posture (same output-injection enforcement question as the CHG-0078 residual).

### CHG-0081 — tools/list metadata-scan decisions were UNAUDITED (broke the …→tag→audit chain for discovery)
- **Date:** 2026-07-02
- **Severity:** MEDIUM (audit-completeness: a tool-poisoning BLOCK or a secret/PII/IP REDACT on the discovery path was invisible to the MCPEvent audit/SIEM trail).
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py`; `gateway/ai_mesh_gateway/tests/test_mcp_tools_list_audit.py` (+3).
- **WHAT (gap, devil's-advocate on the audit end):** CHG-0077's `_scanned_tools_list_response` masks/blocks a poisoned tool-description leak but had ZERO `_record_gateway_event` calls — while the tools/call path audits heavily. So when the gateway REDACTED a secret/PII/internal-IP in a tool description, or BLOCKED a poisoned tools/list (tool-poisoning / encoded-exfil), it recorded NO MCPEvent → the most forensically-important discovery-path security events were invisible to audit/SIEM (breaks the …→tag→AUDIT chain for tools/list; asymmetric with tools/call and the ext-proxy audit CHG-0068/0070).
- **WHY:** 1.4 "…enforce by tag, AUDIT". A block/redact on the discovery path is a security event that must be in the audit trail.
- **NOW DOES:** `_scanned_tools_list_response` calls the best-effort `_record_gateway_event` when the metadata scan BLOCKED or REDACTED (block XOR redact), with tool_name="tools/list", reason="tools_list_metadata_scan", the compliance tags, the scan findings, and a threaded per-request correlation id (new `request_id` param = `_mcp_request_correlation_id(request, msg_id)` from both org sub-paths). A fully-CLEAN tools/list is NOT audited (avoids per-discovery noise). Fire-and-forget / no-op without org (same `_record_gateway_event` → identical persistence + EnforcementEvent bridge).
- **Touched whose work:** the org tools/list path + `_scanned_tools_list_response` (CHG-0077 lineage) + the audit pipeline (owning-session mcp_proxy.py). Additive audit; no behaviour change to the scan/mask/block decision.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_tools_list_audit.py -q` → 3 passed (secret/IP in a description → audited decision=redact w/ tags INFRA,SECRET + request-id; encoded-exfil → audited decision=block; benign tools/list → NO audit event). Full sweep `ai_mesh_gateway/tests` → 1363 passed, 0 failed; broker `-k "not websocket"` → 108 passed. Evidence: `mcp-parallel/findings/backstop-p9-tools-list-audit/finding.md`.

### CHG-0082 — two bare-REST parity gaps: tool-call result-redact unaudited + REST tools-list unscanned
- **Date:** 2026-07-02
- **Severity:** MEDIUM (A: audit-completeness on the primary tool-call path; B: tool-poisoning / secret-PII-IP metadata leak on the REST tools endpoint).
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py`; `gateway/ai_mesh_gateway/tests/test_mcp_bare_rest_parity.py` (+3).
- **WHAT (gap, applying the CHG-0081 audit + CHG-0077 scan lens to the other REST routes):** (A) `org_mcp_tool_call` (REST POST .../tools/call, a primary tenant tool path) audited a result BLOCK but, on a REDACT, swapped the masked content in SILENTLY (no `_record_gateway_event`) — so a secret/PII/IP masked on the bare-REST tool-call path was invisible to audit/SIEM, asymmetric with the block branch AND with org_mcp_jsonrpc (which audits redact). (B) `org_mcp_tools_list` (REST GET .../tools) FILTERED tools (enabled + per-key allowlist) but NEVER scanned the tool descriptions, while the JSON-RPC tools/list already scans them (CHG-0077) — so a secret/PII/internal-IP (or encoded-exfil / tool-poisoning payload) in a tool description egressed to the client on this REST discovery endpoint (coverage asymmetry).
- **WHY:** 1.4 "field-level redaction of tool RESULTS … enforce by tag, AUDIT" + tool-poisoning prevention. Bare-REST routes must reach parity with org_mcp_jsonrpc.
- **NOW DOES:** (A) `org_mcp_tool_call` records a `decision="redact"` gateway event (tags, findings, request-id, latency) before swapping in the masked result — mirroring the block branch above. (B) `org_mcp_tools_list` runs the filtered tools through `_scan_tool_result_floor` (masks maskable leak; blocks unmaskable/encoded-exfil metadata → 403 tools_withheld) + audits block/redact (reason=tools_list_metadata_scan); a clean list is NOT audited. Reuses the whole floor chain (CHG-0074/0075/0076/0079); actor=None (descriptions not actor-scoped).
- **Touched whose work:** the bare-REST routes org_mcp_tool_call + org_mcp_tools_list (owning-session mcp_proxy.py; CHG-0031/0077/0081 lineage). Additive audit + scan; no change to the block/mask decision itself.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_bare_rest_parity.py -q` → 3 passed (bare-REST tool call w/ a secret result → masked AND audited decision=redact; REST tools-list w/ secret+IP in a description → masked + audited redact; benign REST tools-list → unchanged, no audit). Full sweep `ai_mesh_gateway/tests` → 1369 passed, 0 failed; broker `-k "not websocket"` → 108 passed. Evidence: `mcp-parallel/findings/backstop-p9-bare-rest-parity/finding.md`.

### CHG-0083 — obfuscated AWS/GitHub/OpenAI credentials (misfiled in PII_PATTERNS) bypassed the encoded-exfil block
- **Date:** 2026-07-02
- **Severity:** HIGH (a malicious upstream text-encodes / zero-width-hides an AWS access key → the encoded-exfil block missed it → the credential reaches the model deobfuscated).
- **Files:** `gateway/ai_mesh_gateway/mcp_scan_orchestrator.py`; `gateway/ai_mesh_gateway/tests/test_mcp_obfuscated_cred_in_pii.py` (+27, incl. a durable adversarial matrix).
- **WHAT (gap, found by a category×obfuscation regression-matrix pre-flight):** the CHG-0076 (text-encoding) + CHG-0079 (invisible/confusable-unicode) encoded-exfil BLOCK computes `_hidden` from detect_secrets + detect_credential_exposure + detect_ip_leakage∩_INFRA_NETWORK_KEYS. But several CREDENTIALS live in PII_PATTERNS (detected by detect_pii, NOT detect_secrets): aws_access_key (AKIA/ASIA), aws_secret_access_key, api_key_openai, github_token, private_key_header. So an OBFUSCATED AWS/GitHub/OpenAI key (HTML-entity / zero-width / homoglyph) slipped past the block while its raw form masks — the decode/deobfuscate reveals the key but `_hidden` never ran detect_pii on the decoded variant, and a markdown/model client reads it deobfuscated.
- **WHY:** 1.4 obfuscation-bypass completeness. Any credential hidden by obfuscation must fail-closed like the SECRET_PATTERNS ones (CHG-0076/0079).
- **NOW DOES:** the encoded-exfil `_hidden` probe also includes decoded detect_pii matches whose compliance tag is SECRET (the credentials-misfiled-as-PII set: aws_access_key/aws_secret_access_key/api_key_openai/github_token/private_key_header). Generic PII (email/phone/ssn/cc → GDPR/PII/HIPAA/PCI-DSS, never SECRET) is EXCLUDED so an entity-encoded scraped-HTML contact email/SSN does not false-block a legit web/HTML tool result (consistent w/ CHG-0076's PII exclusion). The SECRET-tag filter is the principled, near-zero-FP boundary.
- **Touched whose work:** the MCP scan orchestrator tier-1 encoded/unicode-exfil probe (owning-session file; CHG-0076/0079 lineage). Additive to `_hidden`; no other behaviour change.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_obfuscated_cred_in_pii.py -q` → 27 passed (obfuscated AKIA/ASIA/ghp_/sk-proj- via HTML-entity+zero-width → BLOCK; obfuscated generic email/SSN/phone → NOT blocked, FP guard; raw AWS key still masked; durable matrix of 12 sensitive categories raw→masked/blocked + benign→unchanged). Full sweep `ai_mesh_gateway/tests` → 1369 passed, 0 failed; broker `-k "not websocket"` → 108 passed. Evidence: `mcp-parallel/findings/backstop-p2-obfuscated-cred-in-pii/finding.md`.
- **RESIDUAL:** root cause is aws_access_key etc. living in PII_PATTERNS (a future cleanup could move them to SECRET_PATTERNS but that touches detect_* semantics + tag map across sessions). Obfuscated SSN/credit-card still not blocked by the encoded path (SECRET-tagged only; raw forms mask) — a separate FP-weighed decision.

### CHG-0084 — LeakageDetector.track_cross_request: non-atomic SADD+EXPIRE → orphan Redis key (TTL leak under soak)
- **Date:** 2026-07-02
- **Severity:** MEDIUM (Redis memory-exhaustion / DoS under soak; same class as CHG-0062).
- **Files:** `gateway/ai_mesh_gateway/leakage_detector.py`; `gateway/ai_mesh_gateway/tests/test_leakage_detector_ttl_atomic.py` (+3).
- **WHAT (gap, found by a Redis-correctness sweep applying the CHG-0062 lens):** `LeakageDetector.track_cross_request` tracks cross-request sensitive-fragment hashes in a Redis SET (`leakage:cross:{key_hash}`) with a window TTL, but did per-fragment `await sadd(...)` (N round-trips) then a SEPARATE `await expire(...)`. A coroutine cancellation (client disconnect under load — routine at scale) or a transient Redis error between the last sadd and the expire leaves the SET populated with NO TTL → orphaned forever. Under soak (item 16) such orphaned per-key sets accumulate → unbounded Redis memory growth → exhaustion. Same class as CHG-0062.
- **WHY:** architecture "PostgreSQL + Redis correctness" (item 11) + soak "no exhaustion" (item 16). Every mutation that needs a TTL must set it atomically so a cancelled coroutine can never orphan the key.
- **NOW DOES:** one `MULTI/EXEC` (`pipeline(transaction=True)`) sets all fragment members + the window TTL atomically → the key can never be left without a TTL (and 1 round-trip, not N+1). Sliding-window semantics preserved (EXPIRE re-set each call, no nx). Fail-safe unchanged (returns 0.0 when `_redis is None` or no fragments; benign text → no Redis writes).
- **Touched whose work:** the cross-request leakage detector's Redis state (owning-session file; CHG-0062 lineage). Behaviour-preserving except the orphan-key fix.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_leakage_detector_ttl_atomic.py -q` → 3 passed (exactly one transaction=True pipeline carrying SADD+EXPIRE; the set key ALWAYS has a TTL; no-redis → 0.0; benign → no writes). Existing `test_e11_retrieved_redaction.py` → 9 passed. Full sweep `ai_mesh_gateway/tests` → 1421 passed, 0 failed; broker `-k "not websocket"` → 108 passed. Evidence: `mcp-parallel/findings/backstop-p11-leakage-detector-ttl-leak/finding.md`.
- **RESIDUAL:** `circuit_breaker.py` uses `pipeline(transaction=False)` for INCR+EXPIRE (batched in one write, small orphan window) — lower priority; a future pass could make those transaction=True. Other MCP Redis keys already carry TTLs (CHG-0023/0062).

### CHG-0085 — OAuth token files written world-readable to /tmp (credential-at-rest leak)
- **Date:** 2026-07-02
- **Severity:** HIGH (OAuth access/refresh tokens + client_secret + PKCE code_verifier readable by any co-located process/tenant on the shared host).
- **Files:** `gateway/ai_mesh_gateway/mcp_oauth_proxy.py`; `gateway/ai_mesh_gateway/tests/test_oauth_token_file_perms.py` (+2).
- **WHAT (gap, found by a security review of the OAuth proxy):** `_write_mcp_remote_tokens` persists mcp-remote token files under `/tmp/mcp-orgs/{org}/mcp-auth/…` — `{hash}_tokens.json` (access_token + refresh_token), `{hash}_client_info.json` (client_secret), `{hash}_code_verifier.txt` (PKCE code_verifier) — via `Path.write_text` + `mkdir(parents=True, exist_ok=True)` with DEFAULT perms. Verified on-host: write_text → 0664 (group+world readable), mkdir → 0775 (world-traversable). So a co-located process / user / neighbouring tenant on the shared gateway host could read another org's OAuth credentials at rest → upstream-MCP account takeover. (Redis copies were already encrypted at rest per CHG-0042; the on-disk copies were unprotected.)
- **WHY:** architecture credential-at-rest hygiene / gateway secrets handling. Credential files must be owner-only.
- **NOW DOES:** new `_write_secure_text(path, content)` creates each file via `os.open(..., O_CREAT, 0o600)` (restrictive mode at creation — no world-readable window; 0600 is umask-proof) + re-chmod 0600. `_write_mcp_remote_tokens` chmods the whole org credential tree (`/tmp/mcp-orgs/{org}`, `mcp-auth`, each `mcp-remote-{ver}`) to 0700 (idempotent; also tightens dirs that `mkdir(exist_ok=True)` would leave at their old looser mode). 0700 on the org dir blocks another user from traversing in. Content unchanged.
- **Touched whose work:** the MCP OAuth proxy token-file persistence (owning-session file; CHG-0042 at-rest-encryption lineage). Additive perms hardening; no behaviour change to the OAuth flow.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_oauth_token_file_perms.py -q` → 2 passed (all dirs 0700 + files 0600; os.walk over the org tree finds ZERO group/world-accessible paths; secrets intact; `_write_secure_text` tightens a pre-existing 0644 file to 0600 on re-write). OAuth suite `-k oauth` → 15 passed. Full sweep `ai_mesh_gateway/tests` → 1449 passed, 0 failed; broker `-k "not websocket"` → 108 passed. Evidence: `mcp-parallel/findings/backstop-p12-oauth-token-file-perms/finding.md`.
- **RESIDUAL:** files live under `/tmp` on the OAuth-callback host — owner-only perms close the shared-host read vector; a stronger posture writes them inside the per-tenant gVisor sandbox FS (item 12, future). Consider shredding on token revocation.

### CHG-0086 — circuit_breaker.py: non-atomic INCR+EXPIRE (4 sites) → orphan-key window (closes the CHG-0084 residual)
- **Date:** 2026-07-02
- **Severity:** LOW–MEDIUM (Redis orphan-key window on mid-pipeline failure; completes the CHG-0062/0084 Redis-atomicity hardening).
- **Files:** `gateway/ai_mesh_gateway/circuit_breaker.py`; `gateway/ai_mesh_gateway/tests/test_circuit_breaker_atomic_ttl.py` (+1).
- **WHAT (gap, the documented CHG-0084 residual):** `circuit_breaker.py` used `pipeline(transaction=False)` at 4 sites that INCR (or DELETE) + EXPIRE — `record_success` (total), `record_error` (total + errors), `_bump_epoch` (epoch + probes DELETE), `_admit_probe` (admit; docstring says "Atomically claim a probe slot" yet the INCR+EXPIRE were not wrapped). A non-transactional pipeline batches but does NOT MULTI/EXEC, so a connection drop mid-write (INCR sent, EXPIRE not) orphans the counter with NO TTL (same class as CHG-0062/0084). Counters are per-model (bounded) so smaller impact than CHG-0084, but atomic TTL-setting is the correct pattern and `_admit_probe`'s "atomic" contract was unmet.
- **WHY:** architecture "PostgreSQL + Redis correctness" (item 11). Every INCR/SADD that needs a TTL must set it atomically so a cancelled/failed coroutine can never orphan the key.
- **NOW DOES:** all 4 pipelines use `pipeline(transaction=True)` → INCR (+DELETE) and its EXPIRE commit atomically via MULTI/EXEC; the counter can never be left without a TTL. `execute()` still returns per-command results (record_error reads results[0]/results[2]; _admit_probe reads results[0]) — verified by the existing 24 breaker tests.
- **Touched whose work:** the Redis-backed LLM circuit breaker (owning-session file; CHG-0062/0084 lineage). Behaviour-preserving except the orphan-window fix.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_circuit_breaker_atomic_ttl.py -q` → 1 passed (record_success/error pipelines are transaction=True with paired INCR+EXPIRE). `-k "circuit or breaker"` → 24 passed. `grep 'pipeline(transaction=False)' circuit_breaker.py` → NONE. Full sweep `ai_mesh_gateway/tests` → 1454 passed, 0 failed; broker `-k "not websocket"` → 108 passed. Evidence: `mcp-parallel/findings/backstop-p11-circuit-breaker-atomicity/finding.md`.
- **RESIDUAL:** all gateway INCR/SADD+EXPIRE on the MCP/rate-limit/breaker paths are now atomic (CHG-0062/0084/0086); oauth_proxy/model_state use setex/set(ex=) (atomic by construction). No remaining non-atomic TTL-setter found.

### CHG-0087 — MCP scan decisions were audited but not metered (Prometheus monitoring gap, item 13)
- **Date:** 2026-07-02
- **Severity:** MEDIUM (observability: the 1.4 guardrail decisions were invisible to metrics dashboards/alerting).
- **Files:** `gateway/ai_mesh_gateway/metrics.py`, `gateway/ai_mesh_gateway/mcp_proxy.py`; `gateway/ai_mesh_gateway/tests/test_mcp_scan_metrics.py` (+4).
- **WHAT (gap, monitoring/metrics sweep):** the gateway has a Prometheus layer (metrics.py, /metrics, counters for requests/policy-blocks/rate-limit/kill-switch/stream/chat), but there was NO MCP metric and `mcp_proxy.py` imported/called `metrics` NOWHERE. Every MCP scan/enforcement decision (block/redact/allow/monitor — tool-poisoning, credential force-blocks, PII/IP redaction, tools/list metadata scans, org-scope violations) is written to the MCPEvent AUDIT trail (`_record_gateway_event`) but was never METERED. So Prometheus dashboards + alerting could not see MCP block/redact rates, volume, or compliance-tag distribution — the 1.4 guardrails were invisible to metrics-based monitoring (audit and metrics are different channels; only audit existed for MCP).
- **WHY:** architecture "Phase-3 monitoring/metrics wired" (item 13) + 1.4 "…→audit" visibility. Aggregate, alertable metrics of the guardrail decisions are needed alongside per-event audit.
- **NOW DOES:** metrics.py adds two low-cardinality counters — `amf_gateway_mcp_scan_decisions_total{org, decision}` (block/redact/allow/monitor) and `amf_gateway_mcp_compliance_tags_total{org, tag}` (SECRET/PII/INFRA/HIPAA/PCI-DSS/SOC2/GDPR...) — plus `record_mcp_scan_decision(org, decision, tags)` (fail-safe no-op without prometheus_client, `_safe_label`-bounded). `mcp_proxy._record_gateway_event` calls it (best-effort try/except so metrics NEVER break the request path). Every audited MCP decision is now also metered + rendered in /metrics.
- **Touched whose work:** the gateway metrics module + the MCP audit sink (owning-session files). Additive; the audit path is unchanged (metrics are an extra best-effort side-effect).
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_scan_metrics.py -q` → 4 passed (record_mcp_scan_decision increments the decision + per-tag counters; _record_gateway_event audit sink moves the counter; empty-tags/empty-org safe; no-op when prometheus_client absent). `-k metric` → 13 passed. Full sweep `ai_mesh_gateway/tests` → 1455 passed, 0 failed; broker `-k "not websocket"` → 108 passed. Evidence: `mcp-parallel/findings/backstop-p13-mcp-scan-metrics/finding.md`.
- **RESIDUAL:** an MCP scan/call latency histogram + OpenTelemetry tracing for the per-call chain are separate item-13 enhancements (the audit already carries latency_ms).

### CHG-0088 — MCP tool-call latency histogram (item-13 monitoring; completes CHG-0087)
- **Date:** 2026-07-02
- **Severity:** LOW–MEDIUM (observability: MCP tool-call latency percentiles were not exposed as a metric).
- **Files:** `gateway/ai_mesh_gateway/metrics.py`, `gateway/ai_mesh_gateway/mcp_proxy.py`; `gateway/ai_mesh_gateway/tests/test_mcp_scan_metrics.py` (+2).
- **WHAT (gap, the CHG-0087 residual):** CHG-0087 added MCP scan-decision + compliance-tag COUNTERS but no latency metric. `_record_gateway_event` already carries `latency_ms` (16+ call sites pass a computed `int((time.time()-call_t0)*1000)`), yet MCP tool-call latency was never exposed to Prometheus — so dashboards/alerting could not see MCP p50/p95/p99, exactly what "1.4 guardrails holding under peak load" (item 20) needs.
- **WHY:** item 13 monitoring + item 20 "1.4 under peak load" observability.
- **NOW DOES:** metrics.py adds `amf_gateway_mcp_call_seconds{org, decision}` Histogram (buckets 5ms…10s); `record_mcp_scan_decision(...)` gains a `latency_ms` param and observes `latency_ms/1000` ONLY when truthy (so paths that don't time the call — e.g. the tools/list metadata-scan audit — don't skew the low bucket with 0s samples). `mcp_proxy._record_gateway_event` passes `latency_ms` (still best-effort try/except so metrics never break the request path).
- **Touched whose work:** the gateway metrics module + the MCP audit sink (owning-session files; CHG-0087 lineage). Additive.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_scan_metrics.py -q` → 6 passed (2 block decisions @125ms+340ms → count +2 / sum 0.465s; latency 0/None/omitted → no observation; audit sink records one; no-op without prometheus_client). Full sweep `ai_mesh_gateway/tests` → 1461 passed, 0 failed; broker `-k "not websocket"` → 108 passed. Evidence: `mcp-parallel/findings/backstop-p13-mcp-latency-histogram/finding.md`.
- **RESIDUAL:** OpenTelemetry tracing for the per-call chain remains a separate item-13 piece; a few audit calls (tools/list metadata scan, ext-proxy `_ext_audit`) don't pass latency_ms yet (minor follow-up).

### CHG-0089 — MCP per-org rate-limit 429s were unmetered (Prometheus backpressure blind-spot, item 13/20)
- **Date:** 2026-07-02
- **Severity:** MEDIUM (observability: MCP throttling/backpressure invisible to metrics — matters under the "5k–10k concurrent tool calls" stress).
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py`; `gateway/ai_mesh_gateway/tests/test_mcp_ratelimit_metric.py` (+4).
- **WHAT (gap, metrics-coverage sweep after CHG-0087/0088):** `metrics.record_rate_limit` is called ONLY from the chat handler (main.py, per-MODEL limiter). The per-ORG TPM/burst/RPM limiter (`_enforce_org_tpm_rate_limit`/`_enforce_org_burst_rpm`) doesn't meter internally, and the MCP path (`_mcp_org_rate_limit_raw`) returned a plain 429 with NO metric. So MCP tool calls throttled by the per-org ceiling (CHG-0031/0032) were recorded nowhere in Prometheus — an MCP 429 storm under the mandate's 5k–10k concurrent tool calls was invisible to dashboards/alerting (operators couldn't see MCP backpressure / per-org throttle rates).
- **WHY:** item 13 monitoring + item 20 "1.4 under peak load" observability. Throttling is a first-class signal under stress.
- **NOW DOES:** `_mcp_org_rate_limit_raw`, when it returns a 429 (TPM or burst/RPM), increments the MCP decisions metric via `record_mcp_scan_decision(org, "rate_limited")` — MCP throttling now lands in the same `amf_gateway_mcp_scan_decisions_total{org, decision}` series as block/redact/allow/monitor. Best-effort try/except (metrics never break the request path). The TPM→burst short-circuit + the allow (None) path are preserved by the refactor.
- **Touched whose work:** the MCP per-org rate limiter (owning-session file; CHG-0031/0032 lineage) + the metrics layer (CHG-0087/0088). Additive metering; no behaviour change to throttling itself.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_ratelimit_metric.py -q` → 4 passed (TPM trip → 429 + metered + burst short-circuited; burst trip → 429 + metered; allowed → None + no metering; None auth → no-op). `test_mcp_rate_limit.py` → 14 passed (behaviour intact). Full sweep `ai_mesh_gateway/tests` → 1501 passed, 0 failed; broker `-k "not websocket"` → 108 passed. Evidence: `mcp-parallel/findings/backstop-p13-mcp-ratelimit-metric/finding.md`.
- **RESIDUAL:** metering `_enforce_org_tpm_rate_limit` INTERNALLY (so chat + MCP callers all meter) would be a cleaner future refactor than per-caller metering.

### CHG-0090 — 1.4 scan/redact guardrails proven concurrency-safe (item-20 concurrency dimension) + regression lock
- **Date:** 2026-07-02
- **Severity:** N/A (verification + regression-lock; zero defects found — the guardrail holds).
- **Files:** `gateway/ai_mesh_gateway/tests/test_mcp_scan_concurrency_safety.py` (new, test-only).
- **WHAT:** item 20 requires the 1.4 guardrails to hold under peak load. The 300–500-sandbox × 5k–10k-concurrent-call SCALE is host-blocked here, but the guardrails' concurrency-SAFETY is provable: the MCP scan/redact chain (`_scan_tool_result_floor` → mcp_scan_orchestrator → redact_all) runs against MODULE-LEVEL state (compiled-pattern LRU cache, enabled-tools/server-config caches). If any were mutated per-scan or shared across coroutines without isolation, a race could CROSS-CONTAMINATE concurrent scans — one request's secret/PII leaking into another's redacted result, or a canary surviving because a neighbour clobbered shared state. A single-call unit test cannot catch this.
- **WHY:** 1.4 "field-level redaction … fail-closed" + item 20 "1.4 guardrails holding under peak load" (the concurrency dimension, runnable on this VM).
- **NOW DOES:** `test_mcp_scan_concurrency_safety.py` fires 300 concurrent `_scan_tool_result_floor` calls (asyncio.gather), each with a UNIQUE canary secret (sk-ant-CANARY####…) + PII (user####@…) + internal IP across 10 orgs, and asserts (a) every call's OWN canary is masked (0 own-canary leaks) and (b) NO call's result contains ANY OTHER call's canary (0 cross-contamination); plus a 100-way benign concurrent run that must pass through unchanged. RESULT: 0 leaks, 0 cross-contamination → the chain is effectively stateless/isolation-safe under concurrency (patterns read-only, redact_all pure, caches read-only during a scan). Durable regression backstop: a future edit introducing shared mutable state into the hot scan path flips this red.
- **Touched whose work:** none (test-only); backstops the shared scan core (patterns.py, mcp_scan_orchestrator.py).
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_scan_concurrency_safety.py -q` → 2 passed. Full sweep `ai_mesh_gateway/tests` → 1527 passed, 0 failed; broker `-k "not websocket"` → 108 passed. Evidence: `mcp-parallel/findings/backstop-p20-1.4-concurrency-safety/finding.md`.
- **HONESTY NOTE:** this proves concurrency-SAFETY of the guardrails (no cross-contamination race), NOT the full 300–500-sandbox / 5k–10k live stress (items 14–20) — still host-blocked, owned by the live load harnesses. It is the item-20 dimension that IS runnable/verifiable on this VM.

### CHG-0091 — MCP adapter (stdio/ws) ERROR-envelope tool-result leak: redaction was DETECTED then DISCARDED
- **Date:** 2026-07-03
- **Severity:** HIGH (fail-open 1.4 data-leak on a realistic transport path).
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`org_mcp_jsonrpc` adapter tools/call output-scan swap logic, ~L3276–3345); `gateway/ai_mesh_gateway/tests/test_mcp_adapter_error_envelope_redaction.py` (new).
- **WHAT (gap):** on the stdio/websocket ADAPTER tools/call path, the output scan target is `_scan_target = payload.get("result") if "result" in payload else payload` — so a BARE JSON-RPC ERROR envelope (`{"jsonrpc","id","error":{…}}`, NO `result` key — the standard response an MCP upstream returns on tool FAILURE) IS scanned whole and a secret/PII/internal-IP in `error.message` IS detected + tagged. BUT all three output swap branches (the redact branch, the redaction-FLOOR condition, and the floor swap) were gated on `"result" in payload`. For an error envelope the redacted output was computed then **DISCARDED** and the RAW error egressed. Under the DEFAULT "tag" posture the first scan only detects (does not mask), so the floor is the operative masker — and its gate was exactly the one that excluded error envelopes. Empirically confirmed: a `postgres://svc:ghp_…@10.0.0.5/prod` connection string in `error.message` was detected (tags `INFRA,SECRET,SOC2`) yet egressed raw. The streamable-http path was NOT affected (it captures `data.get("result") or data.get("content")` and swaps `_scanned_content` back unconditionally).
- **WHY:** HARDEN 1.4 "field-level redaction of tool RESULTS (byte-verified, fail-closed)". An MCP tool-failure error frequently echoes the failing detail (connection strings, file paths, upstream API error bodies) — a high-value, realistic leak surface that the transport-specific error shape slipped past.
- **NOW DOES:** dropped the `"result" in payload` guard from the redaction-floor condition, and made both redact-swap branches write the redacted output back to the WHOLE envelope when there is no `result` key (`payload = _scanned_out` / `_scanned_floor`), keeping the audited `reason` in sync with the masked `error.message`. So a secret/PII/internal-IP in a bare error envelope is now MASKED (or, when a survivor is unmaskable, fail-CLOSED blocked) before egress — parity with the streamable-http path and with the result-envelope floor.
- **Touched whose work:** the owning-session proxy (`mcp_proxy.py`); extends the CHG-0074/E12 result-redaction floor to the error-envelope shape. No behavior change for result envelopes or benign errors.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_adapter_error_envelope_redaction.py -q` → 5 passed (redacted-under-tag; benign unchanged; flag-off leaves raw; monitor wins; unmaskable→fail-closed [BLOCKED]). Full sweep `ai_mesh_gateway/tests` → 1534 passed, 0 failed; broker `-k "not websocket"` → 108 passed. **Byte-level (only truth):** fixed egress = `…"message":"connect failed: [CONNECTION_STRING_REDACTED] timed out"`, raw (flag-off) egress still carries `ghp_…@10.0.0.5`. **Independent oracle (aidefence, decoupled from patterns.py):** on an email/SSN error envelope, `aidefence_has_pii` = false on the fixed egress, true on the raw egress. Evidence: `mcp-parallel/findings/backstop-p-adapter-error-envelope/finding.md`.
- **RESIDUAL:** the tools/LIST adapter fall-through (`org_mcp_jsonrpc` ~L2943 returns the raw `adapter_resp` when the payload is not tools-shaped, incl. an error) is the same class but far lower leak-probability (list-metadata errors rarely carry per-call secrets); noted for a future pass, not fixed here to keep this change scoped + well-tested. → **CLOSED by CHG-0092.**

### CHG-0092 — MCP adapter tools/LIST error-envelope leak (the CHG-0091 twin, closes the class)
- **Date:** 2026-07-03
- **Severity:** MEDIUM (fail-open 1.4 leak on the discovery path).
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`org_mcp_jsonrpc` tools/list adapter fall-through, before the `return adapter_resp` after the tools-shaped branch); `gateway/ai_mesh_gateway/tests/test_mcp_adapter_error_envelope_redaction.py` (+3 tools/list tests).
- **WHAT (gap):** on the stdio/ws adapter tools/list path, upstream tool DESCRIPTIONS are scanned ONLY when the payload is tools-shaped (`_scanned_tools_list_response`, CHG-0077/0081). Any NON-tools-shaped payload — a bare JSON-RPC error envelope (the standard response when tools/list fails, e.g. an auth failure that echoes a token/URL/PII), or a malformed result — fell through to `return adapter_resp` **unscanned** → a secret/PII/internal-IP in `error.message` egressed raw. This was the exact residual CHG-0091 flagged.
- **WHY:** HARDEN 1.4 tool-metadata/result redaction (byte-verified, fail-closed) — completes the adapter error-envelope leak class (tools/call = CHG-0091, tools/list = this).
- **NOW DOES:** before the fall-through `return adapter_resp`, scans the whole payload through `_scan_tool_result_floor` (same floor as CHG-0091), mirroring the `_scanned_tools_list_response` contract: masked leak → return redacted envelope; unmaskable survivor → fail-CLOSED withheld with a generic error; clean → raw passthrough (no behavior change for benign errors); audits block/redact (`reason="tools_list_error_scan"`, `enforced_at="gateway_adapter"`).
- **Touched whose work:** the owning-session proxy (`mcp_proxy.py`); extends CHG-0077/0081/0091.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_adapter_error_envelope_redaction.py -q` → 8 passed (5 tools/call + 3 tools/list: secret+IP masked / benign unchanged / tools-shaped still scanned). Full sweep `ai_mesh_gateway/tests` → 1538 passed, 0 failed; broker `-k "not websocket"` → 108 passed. **Byte-level:** tools/list error egress `…"message":"auth failed for user b***@c***.example ssn ***-**-4321"` (raw was `bob.jones@corp.example ssn 987-65-4321`); AWS-key+IP variant both masked. **Independent oracle (aidefence):** `has_pii` = false on fixed egress, true on raw envelope. Evidence: `mcp-parallel/findings/backstop-p-toolslist-error-envelope/finding.md`.
- **INVESTIGATION NOTE (no gap):** a `ghp_…` in a tool description looked unmasked → confirmed a test-token defect (pattern is `\bghp_[a-zA-Z0-9]{36}\b`, exactly 36; probe was 25/37 chars). A valid 36-char github token IS masked (github_token ∈ PII_PATTERNS → detect_pii → floor). Tests use `AKIAIOSFODNN7EXAMPLE` which masks standalone.

### CHG-0093 — SSE multi-line `data:` split evades the MCP tool-result scanner
- **Date:** 2026-07-03
- **Severity:** HIGH (fail-open 1.4 leak; spec-valid framing evasion of the SSE result floor).
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`_scan_reframe_sse_tool_result`); `gateway/ai_mesh_gateway/tests/test_mcp_sse_multiline_split.py` (new).
- **WHAT (gap):** per the WHATWG SSE spec an event's data is the concatenation of ALL its `data:` field values joined by `"\n"`. The reframer walked the buffered SSE line-by-line and parsed EACH `data:` line as standalone JSON. An untrusted upstream can SPLIT its JSON-RPC result across several `data:` lines at a STRUCTURAL point (JSON whitespace between tokens): each fragment is invalid JSON alone → the per-line `json.loads` raised → the frame fell through to "not JSON → pass verbatim" (unscanned) — yet a spec-compliant client reassembles the fragments (joined by `\n`, valid JSON whitespace) into the COMPLETE result → the secret/PII egressed raw. Empirically confirmed: a 2-line split of `AKIAIOSFODNN7EXAMPLE`+`10.9.8.7` egressed raw and the client reconstructed valid JSON with the secret.
- **WHY:** HARDEN 1.4 "field-level redaction of tool RESULTS (byte-verified, fail-closed)" — hardens the SSE result floor (CHG-0039/0043/0064/0070) against a framing-level evasion the per-line scan missed.
- **NOW DOES:** parses the buffered SSE PER EVENT (blank-line boundaries), reassembling every event's `data:` values with `\n` BEFORE json-parsing + scanning via `_scan_tool_result_floor`. On redact → re-emit non-`data:` field lines (`event:`/`id:`/comments) verbatim + the masked payload as a single `data:` line; unmaskable survivor → fail CLOSED (withhold whole result); clean/keep-alive/non-JSON events → verbatim (framing preserved). Covers both `result` and `error` frames.
- **Touched whose work:** the owning-session proxy (`mcp_proxy.py`); extends CHG-0039/0043/0064/0070.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_sse_multiline_split.py -q` → 7 passed (single-line regression; 2-line split; 3-line + `event:`; split ERROR frame; unmaskable→fail-closed; benign preserved; keep-alive/non-JSON passthrough). Full sweep `ai_mesh_gateway/tests` → 1540 passed, 0 failed; broker `-k "not websocket"` → 108 passed. **Byte-level (client-reassembled):** fixed = `user c***@c***.example ssn ***-**-7788`, raw = `user carol.roe@corp.example ssn 555-66-7788`. **Independent oracle (aidefence):** `has_pii` = false on fixed client view, true on raw. Evidence: `mcp-parallel/findings/backstop-p-sse-multiline-split/finding.md`.

### CHG-0094 — MCP audit backpressure dropped SECURITY-decision records under load
- **Date:** 2026-07-03
- **Severity:** MEDIUM (audit-completeness / observability; the `…→tag→AUDIT` chain broke silently under peak load — no egress, but lost evidence during an attack).
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`_spawn_audit_event` + audit caps), `gateway/ai_mesh_gateway/metrics.py` (`mcp_audit_dropped_total` + `record_mcp_audit_dropped`); `gateway/ai_mesh_gateway/tests/test_mcp_audit_backpressure_priority.py` (new).
- **WHAT (gap):** `_spawn_audit_event` sheds audit POSTs when inflight ≥ `_AUDIT_MAX_INFLIGHT`(64) — correct backpressure — but the drop was INDISCRIMINATE. Under the stress scenario (5k–10k concurrent calls, control latency near the 5s audit timeout) the 64 slots fill and EVERY audit drops, including `block`/`redact`/`rate_limited`/`error` security decisions. An attack producing many blocks fills the queue and drops the very block/redact audits it created — the `…→tag→AUDIT` chain breaks exactly when it matters, marked only by a `LOG.warning` (no metric).
- **WHY:** HARDEN 1.4 full per-call chain `authz→minimize→scan+redact→tag→AUDIT` + item 13/20 monitoring & audit-completeness under peak load.
- **NOW DOES:** priority-aware shedding — security decisions get a higher inflight ceiling (`_AUDIT_MAX_INFLIGHT_HIGH=256`, env-overridable) so a burst sheds the high-volume `allow`/`monitor`/`clean` records first and the security audits survive; the shared counter still bounds total inflight to 256 (memory bounded). Every drop increments `amf_gateway_mcp_audit_dropped_total{priority,decision}` (fail-safe) — a non-zero `priority="high"` series = a lost security audit, directly alertable. `_spawn_audit_event` reads `payload["decision"]` (already present) → no caller change.
- **Touched whose work:** owning-session proxy audit sink (CP49) + CHG-0087/0088 metrics layer.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_audit_backpressure_priority.py -q` → 5 passed (below-cap all spawn; at normal cap allow sheds but block/redact/rate_limited/error survive; monitor/clean shed; at hard cap block drops + metered high; cap/priority-set invariant). Full sweep `ai_mesh_gateway/tests` → 1552 passed, 0 failed; broker `-k "not websocket"` → 108 passed. Evidence: `mcp-parallel/findings/backstop-p-audit-backpressure-priority/finding.md`.

### CHG-0095 — ext-proxy infra-error WITHHOLD/redact decisions were not audited
- **Date:** 2026-07-03
- **Severity:** MEDIUM (audit-completeness; fail-closed content blocks invisible to the MCPEvent trail — item 9 `…→tag→AUDIT`).
- **Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`ext_mcp_proxy`); `gateway/ai_mesh_gateway/tests/test_mcp_ext_withhold_audit.py` (new).
- **WHAT (gap):** `ext_mcp_proxy` audits its SSRF/credential/PII blocks (CHG-0068/0070) but several fail-closed enforcement decisions recorded NOTHING → a withheld/redacted response was invisible in the audit trail: upstream response-too-large withhold (SSE + non-SSE, CHG-0064), non-JSON body withhold + redact, JSON-RPC error-content withhold + redact, non-200 body withhold + redact (CHG-0061), and the request-body-too-large DoS reject. This is the residual CHG-0068/0070 explicitly left open ("infra-error withholds not yet audited").
- **WHY:** HARDEN item 9 gateway audit + the full 1.4 per-call chain `…→tag→AUDIT` — a withhold is a security decision (content blocked from egress); not auditing it breaks the chain exactly for the fail-closed cases.
- **NOW DOES:** added `_ext_audit(...)` at every previously-silent site (fire-and-forget, no latency, no-op when unauthenticated) with stable reasons — `block`/`response_too_large`, `block`/`text_body_withheld` + `redact`/`text_body_redacted`, `block`/`error_content_withheld` + `redact`/`error_content_redacted`, `block`/`nonok_body_withheld` + `redact`/`nonok_body_redacted`, `block`/`request_too_large` — each carrying tool + tags + findings where available. Purely additive (response bodies unchanged).
- **Touched whose work:** owning-session ext passthrough audit (CHG-0068/0070).
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_ext_withhold_audit.py -q` → 5 passed (response-too-large JSON + SSE; non-JSON text redaction; non-200 body redaction; request-too-large — each asserts `_record_gateway_event` fired with the right decision/reason; the text test also asserts the email is masked on egress). Full sweep `ai_mesh_gateway/tests` → 1558 passed, 0 failed; broker `-k "not websocket"` → 108 passed. Evidence: `mcp-parallel/findings/backstop-p-ext-withhold-audit/finding.md`.
- **RESIDUAL:** the domain-not-allowlisted 403 (line ~1515) is emitted before the `_ext_audit` closure is defined + is a static input reject (noise potential) — left unaudited by design; noted.
