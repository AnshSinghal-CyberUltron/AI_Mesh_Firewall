#!/usr/bin/env bash
# Apply branch protection to develop, staging and main. Needs a token with repo admin rights
# (the repo owner's `gh auth login`); CI tokens cannot change protection.
#
#   ./scripts/ci/setup_branch_protection.sh AnshSinghal-CyberUltron/AI_Mesh_Firewall
#
# Required checks are the job names GitHub shows on a PR. Re-run after renaming a job.
#
# enforce_admins is ON by default. Every agent acts as the owner's (admin) account, so with it
# off every required check would be advisory for every merge made under MERGE_POLICY.md. For an
# emergency the owner can lift it for one merge and restore it:
#   gh api -X DELETE repos/OWNER/REPO/branches/BRANCH/protection/enforce_admins
#   gh api -X POST   repos/OWNER/REPO/branches/BRANCH/protection/enforce_admins
# ENFORCE_ADMINS=false applies the old, advisory behaviour.
#
# required_conversation_resolution is OFF: with enforce_admins it would bind the agents too, and
# review threads can be resolved only through GraphQL (blocked for agents) or the UI, so every PR
# with a code-scanning bot thread would wait for the owner. MERGE_POLICY.md rule 3 already
# requires every finding to be answered.
set -euo pipefail
repo="${1:?usage: $0 owner/repo}"
enforce_admins="${ENFORCE_ADMINS:-true}"
case "$enforce_admins" in true|false) ;; *) echo "ENFORCE_ADMINS must be true or false" >&2; exit 2 ;; esac

# Each required check is bound to the GitHub Actions app (id 15368). A bare context name could
# be satisfied by anyone with write access posting a commit status of the same name.
actions_app_id=15368

required_checks=(
  "control (Django + Postgres + Redis) / django"
  "frontend (unit tests + production build) / node"
  "CI helper scripts"
  "secrets (gitleaks + TruffleHog) / secrets"
  "SAST (Semgrep + Bandit) / semgrep"
  "dependencies (review + OSV + npm audit) / dependency-review"
  "infra lint (actions, Docker, shell, compose, nginx, Trivy) / lint"
  "policy"
  "structural-gates"
  "secret-headers"
  "openai-conformance"
)

checks_json=$(printf '%s\n' "${required_checks[@]}" | APP_ID="$actions_app_id" python3 -c 'import json,os,sys; app=int(os.environ["APP_ID"]); print(json.dumps([{"context": l.strip(), "app_id": app} for l in sys.stdin if l.strip()]))')

echo "This REPLACES any existing protection on develop, staging and main with the settings below."
for branch in develop staging main; do
  if ! gh api "repos/$repo/branches/$branch" --silent 2>/dev/null; then
    echo "WARNING: branch '$branch' does not exist; skipped. Create it, then re-run this script." >&2
    continue
  fi
  echo "Protecting $branch"
  gh api -X PUT "repos/$repo/branches/$branch/protection" --input - <<JSON
{
  "required_status_checks": { "strict": true, "checks": $checks_json },
  "enforce_admins": $enforce_admins,
  "required_pull_request_reviews": {
    "required_approving_review_count": 0,
    "dismiss_stale_reviews": true
  },
  "restrictions": null,
  "required_linear_history": false,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": false
}
JSON
done
echo "Done. Agent reviews are COMMENT reviews (GitHub does not let a PR's author approve it),"
echo "so approvals are enforced by docs/ci/MERGE_POLICY.md, not by a GitHub approval count."
