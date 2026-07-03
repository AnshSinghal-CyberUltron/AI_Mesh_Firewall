# CHG-0148 — name-based field redaction was bypassable by DEEP NESTING (field below max_depth=10 egressed RAW)

**Change-id:** CHG-0148
**Date:** 2026-07-03
**Severity:** MEDIUM (1.4 leak — a policy's `redaction_fields` (named-field RBAC masking of tool RESULTS)
silently failed to mask a target field nested deeper than 10 levels, so the raw value egressed. Opaque
name-redacted fields — e.g. a `session_token` / `internal_id` the operator masks BY NAME — are NOT caught by
the content/pattern scan, so field redaction is the only layer protecting them).
**Area:** HARDEN 1.4 — field-level redaction of tool RESULTS, "byte-verified, fail-closed".
**Files:** `gateway/ai_mesh_gateway/policy_engine.py` (`apply_field_redaction`);
`control/ai_mesh_control/policy/redaction.py` (`apply_field_redaction`);
`gateway/ai_mesh_gateway/tests/test_mcp_scan_orchestrator.py` (+1 test).
**Whose work it touches:** the two parallel `apply_field_redaction` implementations (gateway 3b port +
control HTTP path) — CHG-0024/0025 / item 3b.

## Root cause (empirically confirmed)

Both `apply_field_redaction` implementations walked the tool result with `max_depth: int = 10` and returned
the PARTIALLY-redacted result on overflow ("safety over strictness on adversarial payloads" — a fail-OPEN).
So a target field nested at depth ≥ 11 was never visited → its value egressed RAW.

But the gateway only rejects a result deeper than `_MCP_MAX_RESULT_DEPTH` (**500**, `mcp_proxy.py`) BEFORE
redaction runs — so results at depth 11..500 reach the redactor and evade masking. Empirical probe (pure
import of both functions):

```
node = {"ssn": "SECRET-123"};  for _ in range(40): node = {"wrap": node}
apply_field_redaction(node, ["ssn"])   # OLD → "SECRET-123" still present (LEAK)
```

Content/pattern scanning does not backstop this for OPAQUE fields: a `session_token` masked by NAME is not
a recognizable PII/secret pattern, so tier-1/floor scanning won't catch it. Field redaction is the sole
control — and it was bypassable by wrapping the field in a few dicts.

## The fix (CHG-0148)

1. `max_depth` default 10 → **500** (aligned with `_MCP_MAX_RESULT_DEPTH`), so any result that passes the
   gateway depth guard is fully depth-walked — no depth-11..500 evasion window.
2. The recursive `_walk` → an **iterative** explicit-stack walk. Necessary: a 500-deep recursion would blow
   Python's recursion limit and raise, which would propagate to the caller and fail-OPEN (un-redacted) —
   i.e. raising the depth on the recursive walk would re-introduce the very leak. The iterative walk has no
   recursion-limit exposure and identical masking semantics (key match → placeholder; recurse only into
   non-matched dict/list values; `masked`/identity-on-noop preserved on the gateway variant). Max work is
   unchanged (still bounded by `max_nodes`).

Both implementations (gateway `policy_engine` + control `policy/redaction`) get the identical change so the
chat (control HTTP) path and the stdio/ws adapter path mask identically.

## Verification

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_scan_orchestrator.py -q -k field_redaction  # 8 passed (1 new)
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                                    # 2010 passed, 0 failed
```

`test_apply_field_redaction_deep_nesting_not_a_bypass`: an `ssn` wrapped 40 levels deep is now redacted
(raw absent from `json.dumps`), the exact deep leaf reads `[REDACTED]`, and a wide+deep mix is fully
covered. The existing homoglyph / non-mutating / identity-on-noop tests still pass (semantics preserved).
Control variant verified by the same standalone repro (depth-40 `ssn` now redacted; shallow / list-nested /
no-op all correct) — the control plane has no venv here, but the function is pure-importable via the gateway
venv, so the fix is byte-verified.

## Scope / honesty note — residuals (documented, not closed)

1. **Node-count overflow** (`max_nodes=100_000`): a huge-but-shallow result whose target field appears after
   the 100 000th node still evades (the walk stops and returns partial — fail-open on NODE count). The
   gateway bounds body SIZE (~10 MB) but not node COUNT, and a 10 MB body can exceed 100k small nodes.
   Fully closing this needs a fail-CLOSED signal on overflow (block the result), which reverses a deliberate
   availability choice and requires caller changes at both sites.
2. **`copy.deepcopy` recursion**: the redactor deep-copies the input first; stdlib `copy.deepcopy` is
   recursive and RecursionErrors at ~250 depth — so a result at depth ~250..500 passes the gateway guard,
   reaches the redactor, and crashes the deepcopy → caller fail-open. Pre-existing (independent of this
   change; my iterative walk itself never recurses). Recommendation: lower `_MCP_MAX_RESULT_DEPTH` below the
   deepcopy limit, or fail closed on the RecursionError — a separate change.

This closes the trivial, realistic vector (wrap the field 11+ levels deep). Does not change the host-blocked
live-stress status. Partial coverage is not completion.
