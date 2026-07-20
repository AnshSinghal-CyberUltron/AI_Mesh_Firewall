# BACKSTOP finding — item 21: the MCP guardrail UI does NOT reflect 1.4 compliance tags

CHG-0036 (2026-07-02) — finding only (no code change; the fix spans a control-plane
change I cannot safely gate here + a frontend change that needs Playwright + is owned
by fe-harden). This corrects the fe-harden "item 11 DONE" claim (commit 6658f9fc): the
MCP guardrail simulator does NOT fully reflect the 1.4 surfaces the mandate requires.

## The gap (mandate item 21: "MCP panels reflect 1.4 — tags, redaction indicators")

`frontend/src/components/simulator/MCPGuardrailSimulator.jsx` builds a `verdict`
(lines ~311-354) that captures `action`, `matched_policies`, `matched_rules`,
`redacted_input_args`, `redacted_output`, `blocked` — but NEVER `compliance_tags`.
So the PII/PHI/PCI/GDPR/HIPAA/INFRA framework tags are not shown anywhere in the UI.

Two concrete reflection holes:

1. **Compliance tags dropped in ALL modes.** No `verdict.compliance_tags`, no tag chip
   render. The mandate's "tag inputs + results ... reflect in the panel" is unmet.

2. **LIVE mode (status 2xx branch, lines ~338-345) is nearly blank.** It sets
   `matched_policies: []`, `matched_rules: []`, no `redacted_output`, no tags, message
   "Tool executed by gateway." — so a LIVE tool call that the gateway redacted (CHG-0024/
   0030) or tagged shows NO redaction indicator and NO tags. Only the DRY-RUN mode shows
   `redacted_input_args`/`redacted_output` (lines ~625-644).

## Root cause (why the frontend cannot reflect tags today)

The dry-run endpoint the simulator calls, `POST /api/policies/test/`
(`control/ai_mesh_control/policy/evaluation_views.py` `PolicyTestView`), IMPORTS
`get_compliance_tags` (line 25) but its policy-match response payload (`block`/`redact`/
`monitor`, lines ~551-585) returns `action`, `matched_policies`, `matched_rules`,
`redacted_prompt`, `redacted_response`, `redaction_hints`, `event_id` — and NO
`compliance_tags`. So even a correct frontend cannot display tags: the backend never
sends them.

Similarly the live path `POST /api/mcp-connector/tools/call/` returns the (masked) tool
result + a block envelope, but does not surface `compliance_tags`/`redacted_field_names`
to the client (they live only in the MCPEvent audit).

## Fix (for the owning sessions)

- **control (PolicyTestView + MCPToolCallView):** add `compliance_tags` to the response
  payload, computed from the matched policies' presets via `get_compliance_tags(...)`.
  NB: this intersects the item-5 tag-vocabulary mismatch — the gateway emits
  GDPR/HIPAA/PII/INFRA while the control `COMPLIANCE_FRAMEWORKS`/`ComplianceTag` catalog
  uses GDPR-PII/HIPAA-PHI/… — so unify the vocab (item 5) as part of this, or the chip
  labels will not match the catalog.
- **frontend (MCPGuardrailSimulator.jsx):** capture `compliance_tags: data?.compliance_tags
  || []` in every `verdict` branch; render a chip list (mirror the existing
  `matched_policies` chip block ~582-600). Also enrich the LIVE 2xx branch to read
  `decision`/`redacted_output`/`compliance_tags` from the live response so a live redacted
  call shows the redaction + tags. Gate: `npm run build` + Playwright (assert the tag
  chips render for a PII payload in both themes).

## Why the backstop did not just fix it here

- The control change is not safely gate-able from this session: there is no control venv
  and Django tests need a test DB; "DO NOT FAKE GREEN" ⇒ no ungate-able cross-plane edit.
- The frontend change is owned + actively iterated by fe-harden and its gate requires
  Playwright (no dev server confirmed up); a dormant partial would risk conflict.
- The tag-vocab entanglement (item 5) is an owning-session semantic decision.

## Verify the finding
```
grep -n "compliance_tags" frontend/src/components/simulator/MCPGuardrailSimulator.jsx   # → none
grep -n "compliance_tags" control/ai_mesh_control/policy/evaluation_views.py            # → import only, not in payload
```
