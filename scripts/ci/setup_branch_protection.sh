#!/usr/bin/env bash
# Apply branch protection to develop, staging and main. Needs a token with repo admin rights
# (the repo owner's `gh auth login`); CI tokens cannot change protection.
#
#   ./scripts/ci/setup_branch_protection.sh AnshSinghal-CyberUltron/AI_Mesh_Firewall
#
# Required checks are the job names GitHub shows on a PR. Re-run after renaming a job.
set -euo pipefail
repo="${1:?usage: $0 owner/repo}"

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

contexts_json=$(printf '%s\n' "${required_checks[@]}" | python3 -c 'import json,sys; print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))')

echo "This REPLACES any existing protection on develop, staging and main with the settings below."
for branch in develop staging main; do
  if ! gh api "repos/$repo/branches/$branch" --silent 2>/dev/null; then
    echo "WARNING: branch '$branch' does not exist; skipped. Create it, then re-run this script." >&2
    continue
  fi
  echo "Protecting $branch"
  gh api -X PUT "repos/$repo/branches/$branch/protection" --input - <<JSON
{
  "required_status_checks": { "strict": true, "contexts": $contexts_json },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "required_approving_review_count": 0,
    "dismiss_stale_reviews": true
  },
  "restrictions": null,
  "required_linear_history": false,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
JSON
done
echo "Done. Agent reviews are COMMENT reviews (GitHub does not let a PR's author approve it),"
echo "so approvals are enforced by docs/ci/MERGE_POLICY.md, not by a GitHub approval count."
