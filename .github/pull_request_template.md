## Why
<!-- The problem or requirement, with a link to the issue, runbook card or finding. -->

## What changed
<!-- The change, component by component. Note anything deliberately left out. -->

## Test evidence
<!-- Commands run and their results: unit, integration, live/E2E, load. Paste counts, not adjectives. -->

## Risk and rollback
<!-- What could break, who is affected, how to roll back (revert, flag, config). -->

## Checklist
- [ ] Tests added or updated for every behaviour change, and they fail without the change
- [ ] CI is green, or each red check is shown failing identically on the base branch (written waiver)
- [ ] No secrets, credentials or personal data in code, tests, fixtures or evidence files
- [ ] Docs, runbook card status and env samples updated where behaviour or config changed
- [ ] Two independent reviews recorded (see docs/ci/MERGE_POLICY.md)
