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
