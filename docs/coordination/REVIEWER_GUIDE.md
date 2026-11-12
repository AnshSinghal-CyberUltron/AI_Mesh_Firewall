# Reviewer agent guide (AI_Mesh_Firewall)

Repo: CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall. Branch flow: feature/fix/ci branches -> develop -> staging -> main. `suraj-revamp` -> `revamp` is the one-off integration merge (PR #100).

## Workspace rules
- Local clone: /home/claude/work/amf. Do NOT change its checked-out branch or working tree, and do not touch other worktrees (wt-ci, wt-f1, wt-fixgate, wt-base, wt-f0base).
- Make your own detached worktree: `git -C /home/claude/work/amf fetch -q origin && git -C /home/claude/work/amf worktree add --detach /home/claude/work/rv-<pr>-<you> <sha>`; remove it when done (`git -C /home/claude/work/amf worktree remove --force <path>`).
- Python venvs: gateway_v2 = /home/claude/work/venv-ci/bin/python (has dev, serve, conformance extras; run from `<worktree>/gateway_v2`, e.g. `../../venv-ci/bin/python -m pytest -q tests/...`). Control (Django) = /home/claude/work/venv-ctl312/bin/python (Python 3.12).
- Control tests need Postgres and Redis, which run as containers: amf-pg (127.0.0.1:55432, db `aimesh`, user postgres; read the password with `docker inspect amf-pg --format '{{range .Config.Env}}{{println .}}{{end}}'`) and amf-redis (127.0.0.1:56379). Export DATABASE_URL, REDIS_URL and a random DJANGO_SECRET_KEY, then from `<worktree>/control`: `python manage.py test core policy auth mcp_connector --noinput`. The CI ratchet is `python scripts/ci/known_failures.py --log <log> --baseline .github/ci/control-known-failures.txt` from the repo root.
- The full gateway_v2 suite takes ~14 min on this 2-core host. Owner decision (11 Nov): authors run the full affected suites locally for every code PR; check the PR body records them. As a reviewer, run targeted tests plus anything you need to verify a claim; serialise any run longer than ~2 min CPU with `flock /home/claude/work/.heavy.lock nice -n 10 ...`, and control tests with `flock /home/claude/work/.ctl-db.lock`.
- The host has 2 CPUs shared with other agents: never run two heavy suites at once, and never use `-n` greater than 2.

## GitHub rules
- GraphQL is blocked: use `gh api` REST only.
- Checks: `gh api "repos/CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall/commits/<sha>/check-runs?per_page=100" --jq '.check_runs[] | "\(.conclusion // .status)\t\(.name)"'`.
- Post a review: `gh api -X POST repos/CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall/pulls/<n>/reviews -F body=@<file> -f event=COMMENT -f commit_id=<full head sha>`. Use CAPITAL `-F body=@file`, so the file CONTENT is sent. A lowercase `-f` posts the literal path, which has happened before. Read the review back and confirm the body is correct.
- The PR author account cannot APPROVE its own PR. Reviews are therefore `event=COMMENT`, with the verdict on the first line: `**Reviewer <X> (<focus>): APPROVE**` or `**... : REQUEST CHANGES**`.
- Never put secrets in reviews or comments. Real-looking credentials in old commits are a known, owner-acknowledged issue (docs/ci/SECURITY.md); do not re-raise them.

## Merge policy (docs/ci/MERGE_POLICY.md)
- Merge only when ALL of these hold:
  1. Every check on the head SHA is completed and green. A check that fails identically on the base may be waived, but only with a written reason in the merge comment.
  2. Two agent reviews with verdict APPROVE are on the CURRENT head SHA, or an earlier SHA plus a review of the delta.
  3. No unresolved BLOCKER.
- Merge method: merge commit. `gh api -X PUT repos/CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall/pulls/<n>/merge -f merge_method=merge -f sha=<full head sha> -f commit_title="<title> (#<n>)"`.
- After merging, post a short comment: what was merged, the checks, the approvals, and any waiver with its reason.
- Do NOT merge if any condition fails. Post your review and report what is missing.
