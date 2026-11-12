#!/usr/bin/env bash
# Create or update the `staging` and `production` deployment environments: required reviewers,
# and which branch may deploy to each. The repository OWNER runs it with an admin token
# (`gh auth login`); agents never run it. docs/ci/ENTERPRISE_SETUP.md step 10.
#
#   scripts/ci/setup_environments.sh --dry-run \
#     --staging-reviewers user:OWNER_LOGIN --production-reviewers user:OWNER_LOGIN
#   scripts/ci/setup_environments.sh \
#     --staging-reviewers user:OWNER_LOGIN --production-reviewers user:OWNER_LOGIN,team:release
#
# Promotion is develop -> staging -> main (docs/ci/MERGE_POLICY.md). A develop->staging
# promotion lands on the `staging` branch, which deploys to the `staging` environment; a
# staging->main promotion lands on `main`, which deploys to `production`. So by default:
#   staging     <- branch staging   (--staging-branch to change)
#   production  <- branch main      (--production-branch to change)
# Any other branch is refused by the environment, so a job on a feature branch cannot use the
# environment's secrets or deploy. No workflow deploys through these environments yet; a deploy
# job opts in with `environment: staging` or `environment: production`.
#
# Reviewers: `user:LOGIN`, `team:SLUG` (an organisation team), or `user:#ID` / `team:#ID`.
# At most 6 per environment (GitHub's limit). A deployment waits until one of them approves.
# Defaults: admins may NOT bypass the reviewers (--allow-admin-bypass to change; every agent acts
# as the owner's admin account), self-review is allowed (--prevent-self-review to forbid it; with
# one human account, forbidding it means nobody can approve that account's own deployments).
#
# Idempotent: PUT replaces each environment's settings; branch policies not listed are deleted.
# --dry-run prints every request and changes nothing; it only reads (GET), to resolve reviewer
# logins and slugs to ids, and prints a placeholder when it cannot.
set -euo pipefail

repo="CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall"
staging_reviewers="" production_reviewers=""
staging_branch="staging" production_branch="main"
production_wait=0
dry_run=false prevent_self_review=false admin_bypass=false

usage() { sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed -e '$d' -e 's/^# \{0,1\}//'; cat <<'USAGE'
Options:
  --repo OWNER/REPO               default CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall
  --staging-reviewers LIST        comma-separated user:LOGIN / team:SLUG / user:#ID / team:#ID
  --production-reviewers LIST     same; required
  --staging-branch NAME           default staging
  --production-branch NAME        default main
  --production-wait-minutes N     wait timer before a production deployment starts (default 0)
  --prevent-self-review           a reviewer cannot approve a deployment they triggered
  --allow-admin-bypass            admins may deploy without a reviewer's approval
  --dry-run                       print the requests; change nothing
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) repo="${2:?}"; shift 2 ;;
    --staging-reviewers) staging_reviewers="${2:?}"; shift 2 ;;
    --production-reviewers) production_reviewers="${2:?}"; shift 2 ;;
    --staging-branch) staging_branch="${2:?}"; shift 2 ;;
    --production-branch) production_branch="${2:?}"; shift 2 ;;
    --production-wait-minutes) production_wait="${2:?}"; shift 2 ;;
    --prevent-self-review) prevent_self_review=true; shift ;;
    --allow-admin-bypass) admin_bypass=true; shift ;;
    --dry-run) dry_run=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done

case "$repo" in */*) ;; *) echo "--repo must be OWNER/REPO" >&2; exit 2 ;; esac
case "$production_wait" in ''|*[!0-9]*) echo "--production-wait-minutes must be 0-43200" >&2; exit 2 ;; esac
if [ "$production_wait" -gt 43200 ]; then echo "--production-wait-minutes must be 0-43200" >&2; exit 2; fi
[ -n "$staging_reviewers" ] || { echo "--staging-reviewers is required" >&2; exit 2; }
[ -n "$production_reviewers" ] || { echo "--production-reviewers is required" >&2; exit 2; }
for b in "$staging_branch" "$production_branch"; do
  case "$b" in ''|*[!A-Za-z0-9._/-]*) echo "branch name '$b' has unexpected characters" >&2; exit 2 ;; esac
done
org="${repo%%/*}"

# Prints the GitHub id for one reviewer spec, or a placeholder in --dry-run when the read fails.
reviewer_id() {
  local kind="$1" ref="$2" id
  case "$ref" in
    \#*) id="${ref#\#}"; case "$id" in ''|*[!0-9]*) echo "bad id in $kind:$ref" >&2; return 2 ;; esac
         printf '%s\n' "$id"; return 0 ;;
  esac
  case "$ref" in ''|*[!A-Za-z0-9._-]*) echo "bad name in $kind:$ref" >&2; return 2 ;; esac
  if [ "$kind" = user ]; then
    id="$(gh api "users/$ref" --jq .id 2>/dev/null || true)"
  else
    id="$(gh api "orgs/$org/teams/$ref" --jq .id 2>/dev/null || true)"
  fi
  if [ -z "$id" ]; then
    if $dry_run; then printf '"<id of %s %s, resolved when applied>"\n' "$kind" "$ref"; return 0; fi
    echo "cannot resolve $kind '$ref' (does it exist, and can this token read it?)" >&2
    return 1
  fi
  printf '%s\n' "$id"
}

# Prints the reviewers JSON array for a comma-separated spec list.
reviewers_json() {
  local list="$1" item kind ref id type out="" n=0
  local -a items
  IFS=',' read -r -a items <<<"$list"
  for item in "${items[@]}"; do
    item="${item// /}"
    [ -n "$item" ] || continue
    kind="${item%%:*}"; ref="${item#*:}"
    case "$kind" in user) type=User ;; team) type=Team ;; *) echo "reviewer '$item' must start with user: or team:" >&2; return 2 ;; esac
    # Explicit: errexit does not reach into $(...), and a bad reviewer must stop the run.
    id="$(reviewer_id "$kind" "$ref")" || return $?
    out="${out:+$out,}{\"type\":\"$type\",\"id\":$id}"
    n=$((n + 1))
  done
  if [ "$n" -lt 1 ] || [ "$n" -gt 6 ]; then echo "each environment needs 1-6 reviewers, got $n" >&2; return 2; fi
  printf '[%s]\n' "$out"
}

environment_json() {
  local reviewers="$1" wait="$2"
  printf '{"wait_timer":%s,"prevent_self_review":%s,"can_admins_bypass":%s,"reviewers":%s,"deployment_branch_policy":{"protected_branches":false,"custom_branch_policies":true}}\n' \
    "$wait" "$prevent_self_review" "$admin_bypass" "$reviewers"
}

apply_environment() {
  local env="$1" reviewers="$2" wait="$3" branch="$4" body tmp names name id
  body="$(environment_json "$reviewers" "$wait")"
  if $dry_run; then
    printf 'PUT repos/%s/environments/%s\n%s\n' "$repo" "$env" "$body"
    printf 'deployment branch policies of %s: exactly [{"name":"%s","type":"branch"}]\n\n' "$env" "$branch"
    return 0
  fi
  tmp="$(mktemp)"
  printf '%s\n' "$body" >"$tmp"
  echo "Environment $env: reviewers, admin bypass $admin_bypass, wait ${wait} min" >&2
  gh api -X PUT "repos/$repo/environments/$env" --input "$tmp" --silent
  rm -f "$tmp"
  names="$(gh api --paginate "repos/$repo/environments/$env/deployment-branch-policies?per_page=100" \
    --jq '.branch_policies[] | "\(.id)\t\(.name)\t\(.type // "branch")"')"
  while IFS=$'\t' read -r id name kind; do
    [ -n "$id" ] || continue
    if [ "$name" != "$branch" ] || [ "$kind" != branch ]; then
      echo "  removing deployment policy '$name' ($kind)" >&2
      gh api -X DELETE "repos/$repo/environments/$env/deployment-branch-policies/$id" --silent
    fi
  done <<<"$names"
  if ! printf '%s\n' "$names" | awk -F'\t' -v b="$branch" '$2 == b && $3 == "branch" {found = 1} END {exit !found}'; then
    echo "  allowing branch '$branch'" >&2
    gh api -X POST "repos/$repo/environments/$env/deployment-branch-policies" \
      -f name="$branch" -f type=branch --silent
  fi
  gh api "repos/$repo/environments/$env" \
    --jq '"  now: \([.protection_rules[]?.type] | join(", ")); admins bypass: \(.can_admins_bypass)"' >&2
}

if ! $dry_run && ! command -v gh >/dev/null; then echo "gh (GitHub CLI) is required" >&2; exit 2; fi
staging_json="$(reviewers_json "$staging_reviewers")" || exit $?
production_json="$(reviewers_json "$production_reviewers")" || exit $?
apply_environment staging "$staging_json" 0 "$staging_branch"
apply_environment production "$production_json" "$production_wait" "$production_branch"
$dry_run || echo "Done. A deploy job opts in with 'environment: staging' or 'environment: production'." >&2
