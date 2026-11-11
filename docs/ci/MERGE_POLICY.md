# Merge policy

The repository owner delegates reviewing and merging to the engineering agents (11 Nov 2026). A PR is merged only when **all** of the following hold:

1. **Two independent reviews say APPROVE.**
   - Reviewer A covers correctness and tests. Reviewer B covers security, operations and release.
   - Each reviewer works from the diff and the evidence, not from the author's summary, and records the review on the PR.
   - GitHub does not let a PR's author approve their own PR. Agent reviews are therefore `COMMENT` reviews whose first line states the verdict: `**Reviewer A (correctness & tests): APPROVE**`.
2. **Every check on the head commit passed.** The only exception is a check that fails **identically on the base branch**. A reviewer may waive that check only with a written reason in their review, naming the evidence: the same failing tests on base and head, and the run links.
   - Branch protection enforces the **required** checks for admins too (`enforce_admins`), and every agent acts as the owner's admin account. A waiver therefore applies only to a check that is not required; a failing required check blocks the merge until it is fixed, or until the owner lifts `enforce_admins` for that one merge.
3. **Findings are answered.**
   - Every BLOCKER is fixed.
   - Every SHOULD-FIX is either fixed or answered in a PR comment saying where it is tracked.
   - The author replies to each reviewer comment.
4. **The PR is mergeable,** with no conflicts.
5. **A push after a review that changes behaviour** needs the affected reviewer's re-check. The re-check may be confirmed in the merging reviewer's review.

Reviewer B merges, using a merge commit (`merge_method=merge`), which keeps the history readable and revertible.

## Promotion

| Into | From | Additional gate |
|---|---|---|
| `develop` | feature/fix/ci/docs/chore/refactor/test/perf branches, Dependabot | the rules above |
| `staging` | `develop`, `hotfix/*` | full local end-to-end run of the stack on the staging branch, recorded in the PR |
| `main` | `staging`, `hotfix/*` | a "Release notes" section, and a cutover/rollback plan for user-visible behaviour |

## Current waivers

| Check | Since | Evidence | Owner of the fix |
|---|---|---|---|
| `golden-freeze` (v1 gateway golden tests) | 6 Aug 2026 | 153 identical failures on `revamp` acae58f5 and on `suraj-revamp` bfe2af7a; red on every run of the workflow since 6 Aug | defect-fix feature |
