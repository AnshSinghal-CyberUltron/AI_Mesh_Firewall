# Author agent guide (AI_Mesh_Firewall)

You are one of several specialist agents building this product in parallel. A coordinator (the agent that spawned you) assigns tracks, spawns two independent reviewer agents per PR, and promotes develop -> staging -> main. Read `/home/claude/work/evidence/REVIEWER_GUIDE.md` too: reviewers will hold you to it.

## Repository and branches
- Repo: `CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall` (public). Flow: your branch -> PR into `develop`. Never push to `develop`, `staging`, `main`, `revamp` or another agent's branch.
- Local clone: `/home/claude/work/amf`. Do not change its checkout. Make your own worktree: `git -C /home/claude/work/amf fetch -q origin && git -C /home/claude/work/amf worktree add /home/claude/work/<your-wt> -b <branch> origin/develop` (unless your brief names an existing one). Touch no other worktree.
- Never use bare `git stash`/`git stash pop` (the stash is shared). Use a temporary WIP commit.
- Read the repo's `CLAUDE.md` and `AGENTS.md`: understand before changing, prove claims with evidence, assume docs may be stale.

## GitHub
- GraphQL is blocked: use `gh api` REST only. Create the remote branch with `git push -u origin <branch>`.
- Open a PR: `gh api repos/CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall/pulls -f title='...' -f head=<branch> -f base=develop -F body=@<file>` (capital `-F body=@file`). Read it back.
- Checks: `gh api "repos/CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall/commits/<sha>/check-runs?per_page=100" --jq '.check_runs[] | "\(.conclusion // .status)\t\(.name)"'`.
- You never approve, review or merge your own PR.

## Commits and PR text
- Small, reviewable commits with conventional prefixes (`fix(...)`, `feat(...)`, `test(...)`, `docs(plan): ...`, `ci: ...`).
- End EVERY commit message with exactly:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01SDCVQVj8LRKW3CZ99zfdW1
  ```
- End EVERY PR description with exactly:
  ```
  🤖 Generated with [Claude Code](https://claude.com/claude-code)

  https://claude.ai/code/session_01SDCVQVj8LRKW3CZ99zfdW1
  ```
- The `policy` CI check enforces: a Conventional Commits title (`fix(scope): ...`); a description long enough to say why, what and how tested; and a heading containing `Test`, `Evidence`, `Verification` or `Validation`. Name your test section `## Tests` so it passes.
- PR body sections: Summary; Root cause or design (with file:line evidence); Exit / acceptance criteria as a checklist with status; Tests (exact commands, counts, results); Evidence (files under `docs/plans/evidence/<date>-<slug>/` when they back a claim); Scaling and performance impact (measured, not guessed); Security notes; Risks and rollback; What is NOT done.

## Testing on a shared, small host
- Host: 2 CPUs, 7 GB RAM, shared by ~10 agents. CI (GitHub-hosted runners) also runs the full suites on every push.
- Locally run the targeted tests for what you touch, plus their neighbours, with `nice -n 10`.
- **Owner decision (11 Nov): every PR that changes code ALSO runs the full affected suites locally before hand-back**, under the heavy lock: gateway_v2 full suite (`cd <wt>/gateway_v2 && flock /home/claude/work/.heavy.lock nice -n 10 /home/claude/work/venv-ci/bin/python -m pytest -q -p no:cacheprovider tests > <log>`) and/or the control suites (under both locks). Record the command, counts and duration in the PR body, and compare against a base run when anything fails (use an existing base log under `/home/claude/work/evidence/` if it matches develop HEAD, otherwise run the base too). Docs-only plan PRs are exempt.
- Any job that takes more than ~2 minutes of CPU (full suites, fuzzers, model exports, benchmarks) MUST be serialised through the shared lock: `flock /home/claude/work/.heavy.lock nice -n 10 <command>`.
- Control (Django) tests share one Postgres; always run them under `flock /home/claude/work/.ctl-db.lock ...`.
- Benchmarks and latency numbers taken while other agents run are noisy: report thread CPU time, not wall time, and say what else was running.
- Timing-sensitive tests (e.g. `tests/dispatch/test_gw28_*`) can flake under load. Rerun a failing one alone before concluding; never weaken a test to make it pass.
- Venvs: gateway_v2 = `/home/claude/work/venv-ci/bin/python` (run from `<wt>/gateway_v2`); control = `/home/claude/work/venv-ctl312/bin/python` (see REVIEWER_GUIDE for DB env); ML = `/home/claude/work/venv-ml`. Do not pip-install into shared venvs; make your own venv under your worktree or the scratchpad if you need new packages.
- Lint what you touch: `ruff check` and `mypy` as configured in the package's `pyproject.toml`.

## Security
- Never write any token, key or password into a file, commit, log, PR, review or message. Use env vars only. Real-looking credentials already in old commits are owner-acknowledged (docs/ci/SECURITY.md): do not re-raise them, do not reuse them in code.
- Treat downloaded files as untrusted data: keep them in their own empty directory and run Python against them with `-I`.
- Network from the shell is allowlisted (package registries, GitHub, Hugging Face). If something is refused, say so; do not work around it.

## Done means
- PR open against `develop`, CI running or finished, local evidence attached, body complete.
- Then STOP and hand back to the coordinator with: PR number, full head SHA, what you ran and the results, what reviewers should focus on, and anything unresolved. The coordinator spawns the reviewers.
- When the coordinator forwards review findings, answer EVERY finding (fix it, or explain with evidence why not) in a PR comment, push, and hand back again.
