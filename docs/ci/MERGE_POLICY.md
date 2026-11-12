# Merge policy

The repository owner delegates reviewing and merging to the engineering agents (11 Nov 2026). A PR is merged only when **all** of the following hold:

1. **Two independent reviews say APPROVE.**
   - Reviewer A covers correctness and tests. Reviewer B covers security, operations and release.
   - Each reviewer works from the diff and the evidence, not from the author's summary, and records the review on the PR.
   - GitHub does not let a PR's author approve their own PR. Agent reviews are therefore `COMMENT` reviews whose first line states the verdict: `**Reviewer A (correctness & tests): APPROVE**`.
2. **Every check on the head commit passed.** The only exception is a check that fails **identically on the base branch**. A reviewer may waive that check only with a written reason in their review, naming the evidence: the same failing tests on base and head, and the run links.
   - **What GitHub enforces.** Today *(12 Nov 2026)* `develop` and `staging` have no branch protection and no ruleset, so GitHub enforces none of these rules there; the merging reviewer does.
     - Once the owner applies the ruleset from `scripts/ci/setup_rulesets.sh` (`docs/ci/ENTERPRISE_SETUP.md`), every check in `.github/ci/required-checks.txt` is required for everyone. That includes admins and every agent: the ruleset has no bypass actors, and agents act as the owner's admin account.
     - A waiver then applies only to a check that is not required. A failing required check blocks the merge until it is fixed, or until the owner switches the ruleset to `evaluate` for that one merge.
3. **Findings are answered.**
   - Every BLOCKER is fixed.
   - Every SHOULD-FIX is either fixed or answered in a PR comment saying where it is tracked.
   - The author replies to each reviewer comment.
   - Every code-scanning thread on the PR is answered in a PR comment: fixed, dismissed with a reason, or tracked. (Agents cannot resolve threads, so the ruleset does not require resolution; this rule does.)
4. **The PR is mergeable,** with no conflicts.
5. **A push after a review that changes behaviour** needs the affected reviewer's re-check. The re-check may be confirmed in the merging reviewer's review.

Reviewer B merges, using a merge commit (`merge_method=merge`), which keeps the history readable and revertible.

## Up-to-date branches

Once the ruleset is applied, a PR merges only when its head contains the current base ("require branches to be up to date"; `docs/ci/ENTERPRISE_SETUP.md` step 10). Every merge into `develop` therefore puts every other open PR behind.

- **Update only the PR that merges next**, just before merging it, with the REST call `PUT /repos/{owner}/{repo}/pulls/{n}/update-branch`. Do not update every open PR after each merge. Each update is a new head and a full CI cycle: 31 jobs and about 68 billed minutes once the repository is private, about 6 minutes of jobs with free runners and 10–19 minutes at 12 Nov's queue. Draining 10 ready PRs one by one took an estimated 1.7–3.2 hours and 680 billed minutes (Reviewer B on PR #138).
- **A head moved only by `update-branch` counts as reviewed**, with no delta review and no re-check under rule 5, when all of these hold:
  1. the only new commit is the merge of the base that `update-branch` made, with no conflict;
  2. the PR's own changes are unchanged: the file list and each file's `patch` from `GET /repos/{owner}/{repo}/pulls/{n}/files`, recorded before the update, equal the ones after it. If the base changed one of the PR's files, they differ, and the delta review is needed;
  3. every check passed on the new head (rule 2).

  The merging reviewer records all three in the merge comment: the old and new heads, the base SHA, "files and patches unchanged", and the checks. Anything else on the new head (a conflict resolved by hand, any other commit) needs a delta review.
- **Why the ruleset keeps "up to date" on:** `gateway-v2.yml` runs on `push` only for `revamp`, so after a merge nothing re-tests `develop` for gateway_v2. Without "up to date", two PRs that each passed alone could merge into a `develop` that fails. Adding `push` on `develop`, `staging` and `main` to `gateway-v2.yml` would give a post-merge signal (follow-up).

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
