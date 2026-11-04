# GW33 output parity: review (pass 2)

GW33 adds a real REWRITE disposition, output detectors (canary, IP leak, exfiltration, grounding), streaming and strict-path wiring, and control-plane publish. The diff is 119 files (+9289/-281) in 18 layered commits. Pass 1 rejected only for missing evidence. `verification.md` now exists and was read. Suites were not re-run, per instructions.

Watch for: exfil enforcement is live only for orgs in `V2_EXFIL_ORGS` (default empty, design finding L-1, carry into changelog). INCREMENTAL flag-only IP/exfil/grounding findings are reported after bytes are out (documented D-9 trade-off). Live gates L33-1..3 are deferred and not claimed (confirmed in verification.md).

**Verdict**: APPROVED

## High-level view

REWRITE is a first-class disposition ranked between REDACT and BLOCK. The resolver is still the single place that renders templates and picks the winning action, and `resolve` imports only `domain`. Overlapping edits with differing replacements collapse to a REDACT of the union, so the bytes are always the stricter outcome.

Egress stays the byte applier. `apply_decision` calls the new `verify_rewrites` only when the released slice holds a rewrite edit, so non-rewrite decisions take the pre-change path. The verifier checks coverage, expected offsets, retained segments and a survivor check, and fails closed with `rewrite_unverified`.

Verification evidence is credible. The full offline suite had 2 failures, both reproduced identically on the base commit and unrelated to GW33. The 236-test gw33 subset passed. ruff and lint-imports (3 contracts kept) were clean. All seven AST gates exit 0. mypy shows 9 errors from missing optional vector client libraries, identical to base.

<details>
<summary>Issues (2)</summary>

1. **Exfil default-off (confirmed, non-blocking)** — L33-1 is enforced only for orgs in `V2_EXFIL_ORGS`. State this in the changelog and final report.
2. **Pre-existing failures carried (confirmed, non-blocking)** — `test_check_tenant_scale` and `test_secret_headers` fail on base too. The second fails only because of the container mount. Re-confirm in the gate-suite step with a proper git mount.

</details>

<details>
<summary>Details</summary>

## Behavior checked against the evidence and a code spot-check

I read `egress/rewrite_verify.py`, `resolve/rewrite.py` and the `egress/output_guard.py` diff. The verifier raises on any offset mismatch, retained-byte mismatch or surviving original bytes (for originals at or above the minimum length). `apply_decision` is otherwise unchanged, so GW09 byte verification is intact. A grep of `egress` and `resolve` found no import of `gateway_v2.detect` outside a docstring. The `canary` class was added to the egress mirror, while `ip_leak` and `exfil_sink` are deliberately absent per D-9, which keeps the GW12b holdback bound untouched. Property tags are enforced by a gate test, with 16 properties and all iteration constants at or above 100. Property and tag coverage is as the plan requires.

Not independently verified: the control-plane publisher changes and the GW26 omission lift were taken from the passing `test_gw33_publish.py` (10 tests) rather than read line by line.

</details>

<details>
<summary>File map</summary>

Full diff: `git diff suraj-revamp...HEAD` on branch gw33-output-parity. Layers touched: domain, runtime/resources_output, egress, dispatch, resolve, detect/output, plan, edge, audit, control, lint gates, tests.

</details>
