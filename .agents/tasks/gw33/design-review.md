# GW33 design review (revision 5)
Reviewed: `.kiro/specs/gw33-output-parity/{design,requirements,tasks}.md` against `.worktrees/gw33` and the runbook card (GW33 depends on GW09, GW10, GW12).
Verdict: **APPROVED** (0 HIGH, 0 MEDIUM, 1 LOW, 1 NIT).

## Round-4 findings: closed
- M-1 (composer refusal keyed on category): D-2 now drops the refusal. Interaction is defined by identity (D-32): the STRICT shim binds one finding per legacy rule to that rule's id; `ExfilDetector` binds to `out_exfil`. Task 8.4a/8.4b adds the combined-plan install test across all four `compile_plan` sites in INCREMENTAL and STRICT.
- M-2 (customer DETECTOR row joins built-in findings): D-32 stamps `Finding.detector` = platform rule id. I verified `resolve/conflict.py::_rules_for` takes the `by_id` branch before the category join (L83-88), so priority is irrelevant. The `_category_join` exclusion for rules with no built-in family is defence in depth. A resolver test (priority-1000 allow/redact vs `out_exfil` block) is in task 5.6. The new categories (IP_LEAK, HALLUCINATION, CANARY) do not exist in `domain/category.py` yet, and EXFIL is produced by nothing today, so the exclusion changes no pre-card behaviour.
- M-3 (stale keyword/category wording): C4, the validation table and task 8.1 now use `RuleCondition.allow_hosts` and `builtin_output_detector(rule)`.
- L-1, L-2, N-1: addressed (`StreamStats.review_required` in C13; platform rules appended after the `_PRIORITY` stamping; "built-in detector rules" wording).

## Cross-cutting checks
- Layering: new egress modules (`output_errors`, `rewrite_verify`, `rewrite_model`) import `domain` only. Detector hooks (`FinalDetector`, `delta_detector`) are injected types. No egress-to-detect edge.
- Capacity literals: confined to `runtime/resources.py` and the explicitly exempt `runtime/resources_output.py` (D-25, with a planted-violation test).
- Module-level mutable state: only `Final`, `MappingProxyType`, frozenset and frozen dataclasses.
- REWRITE vs GW09 byte-verified redaction: resolver stays the single authority (`Disposition.REWRITE`, `Transformation(kind="rewrite")`). A byte-verify step fails closed with a typed exception.
- GW12b holdback: no new holdback classes. Byte-altering built-in rules are STRICT_WITHHOLD-only (D-9, D-20). Multi-channel decisions aggregate via `combine_decisions`.
- Fail-open: UNAVAILABLE + FAIL_OPEN is reported and sets review, never enforced. `FAIL_CLOSED` is refused on flag-only INCREMENTAL categories. Property 8 covers it.
- GW27 handoff: D-13 builds the response-side bound on top of GW27's clamp without a control-plane change.

## Findings
### L-1 LOW: default-off exfil protection must be visible in the owner-facing summary
D-29 ships `V2_EXFIL_ORGS` empty, so no org gets exfil protection until the owner enables it. The design says this plainly and records it in tasks 11.6/12.5. Make sure the changelog entry and the final report state that L33-1 is live only for orgs listed in `V2_EXFIL_ORGS`, so the parity claim is not read as on-by-default.

### N-1 NIT: D-2 is very long
D-2 packs the legacy window, shim, mixed-plan rule and required tests into one bullet. Splitting it would help the implementer. No behavioural ambiguity found.

## Verified assumptions
- `_rules_for` prefers `by_id` over the category join (read in source).
- `_category_join` is `condition is None or kind is DETECTOR` (read in source).
- `Category` currently has no IP_LEAK/HALLUCINATION/CANARY members, so D-1 is additive.
- Task 8.1/8.4a/8.4b text matches the design.

## Unverified
- I did not re-run the four `compile_plan` call-site enumeration or the publisher `_category` fallback line numbers from round 3. I took them from the prior review. The design is internally consistent about them.
