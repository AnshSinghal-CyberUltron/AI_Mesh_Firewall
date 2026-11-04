# GW33 output parity: review (pass 1)

GW33 adds REWRITE semantics, output detectors (canary, IP leak, exfiltration, grounding), streaming changes, and control-plane publish across 116 files (+9155/-281). The review could not be completed on evidence: the coder's `.agents/tasks/gw33/verification.md` does not exist in the worktree, and a search for any `verification*` file found nothing. The directory holds only `design-review.md`, `design-verdict.json` and `plan.md`.

Watch for: no verification evidence (confirmed). Test, mypy, ruff, import-linter and AST-gate results for this diff cannot be confirmed. This pass did not re-run the suites, as instructed.

**Verdict**: NEEDS_CHANGES

## High-level view

The diff touches every layer: domain vocabulary, plan compiler, resolver, detect/output, egress stream and strict, edge composition, audit, and the control plane. The commit log shows tests were written for each piece, including property-tag and single-authority lint gates. The behavioral claims in scope (byte-verified redaction intact, holdback intact, egress not importing detect, no capacity literal outside resources, no live-gate claims) all need run output to confirm. That output is missing.

<details>
<summary>Issues (1)</summary>

1. **Missing verification evidence (confirmed, blocking)** — `.agents/tasks/gw33/verification.md` is absent. Add it with the full offline suite result, mypy --strict, ruff, import-linter, the AST gates (capacity literals, mutable state, frozen dataclasses, single authority), property-test iteration counts, and the L33 offline-equivalent results. State explicitly which live gates are deferred.

</details>

<details>
<summary>Details</summary>

## Absent evidence blocks the behavioral judgments

The task is to judge behavior from the coder's evidence without re-running suites. With no evidence file, the claims cannot be confirmed. They are REWRITE semantics, GW09 byte-verified redaction, GW12b holdback (`egress/stream.py` has 193 changed lines, `stream_publish.py` is new), resolver as the single authority, and fail-closed paths. Also unconfirmed are the egress-to-detect import boundary, capacity literals (`runtime/resources_output.py` is new and `check_capacity_literals.py` was modified), and the GW26 publisher omission lift. No articulable specific doubt was identified that would justify a narrow spot-check in place of the missing evidence. The rejection is on the missing evidence alone, not on a code defect.

</details>

<details>
<summary>File map</summary>

Full diff: `git diff suraj-revamp...HEAD` on branch gw33-output-parity. Not reviewed file by file in this pass.

</details>
