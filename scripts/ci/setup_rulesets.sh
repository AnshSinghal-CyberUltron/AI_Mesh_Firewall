#!/usr/bin/env bash
# Create or update ONE repository ruleset that protects develop, staging and main.
# The repository OWNER runs it with an admin token (`gh auth login`); agents never run it.
# docs/ci/ENTERPRISE_SETUP.md says when, and what to decide first.
#
#   scripts/ci/setup_rulesets.sh --dry-run              # print the ruleset JSON; no API call
#   scripts/ci/setup_rulesets.sh --verify-sha <sha>     # read-only: did every required check
#                                                       # report success on that commit?
#   scripts/ci/setup_rulesets.sh                        # create or update the ruleset
#
# Idempotent: the ruleset is found by its exact name and replaced (PUT) with the same body, or
# created (POST) when absent. Re-run it after editing .github/ci/required-checks.txt.
#
# Rules in the ruleset (enforcement `active`, NO bypass actors, so admins and every agent acting
# as the owner's admin account are bound too, like enforce_admins on classic protection):
#   deletion, non_fast_forward   the three branches cannot be deleted or force-pushed
#   pull_request                 changes only through PRs; merge commits only (MERGE_POLICY.md);
#                                approvals from AMF_REQUIRED_APPROVALS (default 0, see below)
#   required_status_checks       every name in .github/ci/required-checks.txt, bound to the
#                                GitHub Actions app (integration id 15368); "require branches to
#                                be up to date" on (--no-strict turns it off)
#   copilot_code_review          only with --copilot-review (Copilot reviews each PR and push)
#   merge_queue                  only with --merge-queue; OFF by default, see below
#
# Approvals. Agent reviews are COMMENT reviews posted from the owner's own account, and GitHub
# never counts an author's own approval, so any required count above 0 would block every merge.
# Once two GitHub Apps post the A and B reviews (ENTERPRISE_SETUP.md step 9), re-run with
# AMF_REQUIRED_APPROVALS=2: one identity's approval counts once, so 2 needs two Apps.
#
# Merge queue is OFF by default. With it on, the REST merge endpoint refuses (a PR can only be
# merged by adding it to the queue), and adding a PR to the queue is GraphQL-only
# (`enqueuePullRequest`; `gh pr merge` uses GraphQL too). The agents' GitHub proxy blocks
# GraphQL, so with the queue on no agent could merge anything. Turn it on only when merges can
# be enqueued (owner in the UI, or an App outside the proxy) AND every workflow behind a required
# check also runs on `merge_group` (today none does: their checks would never report for the
# queue's merge commit and the queue would stall).
#
# Never touched: the existing ruleset "Main Protected by Ansh" (id 19609105). Its own rules still
# apply to main on top of this ruleset; see ENTERPRISE_SETUP.md step 11.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall"
checks_file="$here/../../.github/ci/required-checks.txt"
ruleset_name="AMF protected branches: develop, staging, main"
protected_ruleset_ids=" 19609105 "   # "Main Protected by Ansh": read, never written
approvals="${AMF_REQUIRED_APPROVALS:-0}"
enforcement="active"
dry_run=false strict=true copilot=false merge_queue=false with_codeql=true force=false
verify_sha=""

usage() {
  sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed -e '$d' -e 's/^# \{0,1\}//'
  cat <<'USAGE'
Options:
  --repo OWNER/REPO      default CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall
  --dry-run              print the ruleset JSON on stdout and exit; makes no API call
  --verify-sha SHA       read-only: report each required check's result on SHA and exit 1
                         unless all succeeded (use a recent PR head: some checks run on PRs only)
  --approvals N          required approving reviews (default: $AMF_REQUIRED_APPROVALS, else 0)
  --no-strict            do not require PR branches to be up to date with the base
  --copilot-review       add the copilot_code_review rule (review on every push, not drafts)
  --merge-queue          add the merge_queue rule (read the header first)
  --without-codeql       drop the "CodeQL / ..." checks (CodeQL default setup kept)
  --evaluate             enforcement "evaluate": report what would be blocked, block nothing
  --checks-file PATH     default .github/ci/required-checks.txt
  --force                apply even when a required check is failing on develop's head
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) repo="${2:?--repo needs OWNER/REPO}"; shift 2 ;;
    --dry-run) dry_run=true; shift ;;
    --verify-sha) verify_sha="${2:?--verify-sha needs a commit SHA}"; shift 2 ;;
    --approvals) approvals="${2:?--approvals needs a number}"; shift 2 ;;
    --no-strict) strict=false; shift ;;
    --copilot-review) copilot=true; shift ;;
    --merge-queue) merge_queue=true; shift ;;
    --without-codeql) with_codeql=false; shift ;;
    --evaluate) enforcement="evaluate"; shift ;;
    --checks-file) checks_file="${2:?--checks-file needs a path}"; shift 2 ;;
    --force) force=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done

case "$approvals" in ''|*[!0-9]*) echo "--approvals / AMF_REQUIRED_APPROVALS must be 0-10" >&2; exit 2 ;; esac
if [ "$approvals" -gt 10 ]; then echo "--approvals must be 0-10" >&2; exit 2; fi
case "$repo" in */*) ;; *) echo "--repo must be OWNER/REPO" >&2; exit 2 ;; esac
[ -f "$checks_file" ] || { echo "required checks file not found: $checks_file" >&2; exit 2; }

# The ruleset body, built by stdlib Python from the inputs in the environment.
ruleset_json() {
  RS_NAME="$ruleset_name" RS_ENFORCEMENT="$enforcement" RS_APPROVALS="$approvals" \
  RS_STRICT="$strict" RS_COPILOT="$copilot" RS_MERGE_QUEUE="$merge_queue" \
  RS_WITH_CODEQL="$with_codeql" RS_CHECKS_FILE="$checks_file" python3 -I - <<'PY'
import json, os, sys

def flag(name):
    return os.environ[name] == "true"

checks = []
with open(os.environ["RS_CHECKS_FILE"], encoding="utf-8") as fh:
    for raw in fh:
        line = raw.strip()
        if line and not line.startswith("#"):
            checks.append(line)
if not flag("RS_WITH_CODEQL"):
    checks = [c for c in checks if not c.startswith("CodeQL / ")]
dupes = sorted({c for c in checks if checks.count(c) > 1})
if dupes or not checks:
    sys.exit(f"required checks file: duplicates {dupes}" if dupes else "required checks file is empty")

approvals = int(os.environ["RS_APPROVALS"])
rules = [
    {"type": "deletion"},
    {"type": "non_fast_forward"},
    {
        "type": "pull_request",
        "parameters": {
            "required_approving_review_count": approvals,
            "dismiss_stale_reviews_on_push": True,
            "require_code_owner_review": False,
            # With approvals required, the last push must be approved by someone else.
            "require_last_push_approval": approvals > 0,
            # Agents cannot resolve threads (GraphQL); MERGE_POLICY.md rule 3 covers them.
            "required_review_thread_resolution": False,
            "allowed_merge_methods": ["merge"],
        },
    },
    {
        "type": "required_status_checks",
        "parameters": {
            "strict_required_status_checks_policy": flag("RS_STRICT"),
            "do_not_enforce_on_create": False,
            # 15368 = the GitHub Actions app: only an Actions job can satisfy these names.
            "required_status_checks": [{"context": c, "integration_id": 15368} for c in checks],
        },
    },
]
if flag("RS_COPILOT"):
    rules.append(
        {
            "type": "copilot_code_review",
            "parameters": {"review_on_push": True, "review_draft_pull_requests": False},
        }
    )
if flag("RS_MERGE_QUEUE"):
    rules.append(
        {
            "type": "merge_queue",
            "parameters": {
                "check_response_timeout_minutes": 60,
                "grouping_strategy": "ALLGREEN",
                "max_entries_to_build": 5,
                "max_entries_to_merge": 5,
                "merge_method": "MERGE",
                "min_entries_to_merge": 1,
                "min_entries_to_merge_wait_minutes": 5,
            },
        }
    )
body = {
    "name": os.environ["RS_NAME"],
    "target": "branch",
    "enforcement": os.environ["RS_ENFORCEMENT"],
    "bypass_actors": [],
    "conditions": {
        "ref_name": {
            "include": ["refs/heads/develop", "refs/heads/staging", "refs/heads/main"],
            "exclude": [],
        }
    },
    "rules": rules,
}
print(json.dumps(body, indent=2))
PY
}

# Exits 0 when every required name has a successful check run from GitHub Actions on $1.
verify_on_commit() {
  local sha="$1" runs
  runs="$(gh api --paginate "repos/$repo/commits/$sha/check-runs?per_page=100" \
    --jq '.check_runs[] | [.id, .name, (.conclusion // .status), (.app.id // 0)] | @tsv')"
  RUNS="$runs" BODY="$(ruleset_json)" python3 -I - <<'PY'
import json, os, sys

body = json.loads(os.environ["BODY"])
rule = next(r for r in body["rules"] if r["type"] == "required_status_checks")
required = [c["context"] for c in rule["parameters"]["required_status_checks"]]
latest = {}  # name -> (check run id, conclusion or status) of the newest run by GitHub Actions
for line in os.environ["RUNS"].splitlines():
    run_id, name, state, app = line.split("\t")
    if app == "15368" and (name not in latest or int(run_id) > latest[name][0]):
        latest[name] = (int(run_id), state)
bad = 0
for name in required:
    state = latest.get(name, (0, "MISSING"))[1]
    ok = state == "success"
    bad += 0 if ok else 1
    print("%s  %-12s %s" % ("ok  " if ok else "FAIL", state, name))
print("%d of %d required checks succeeded" % (len(required) - bad, len(required)))
sys.exit(1 if bad else 0)
PY
}

body="$(ruleset_json)"

if $dry_run; then
  printf '%s\n' "$body"
  exit 0
fi

if [ -n "$verify_sha" ]; then
  echo "Required checks on $verify_sha in $repo:" >&2
  verify_on_commit "$verify_sha"
  exit 0
fi

if ! command -v gh >/dev/null; then echo "gh (GitHub CLI) is required" >&2; exit 2; fi

echo "Repository: $repo" >&2
for branch in develop staging main; do
  if gh api "repos/$repo/branches/$branch" --silent 2>/dev/null; then
    echo "  branch $branch: exists" >&2
  else
    echo "  branch $branch: MISSING; the ruleset applies once it is created" >&2
  fi
done

# Preflight: a required check that is already failing on develop's head would block every merge.
# Only push-triggered checks (CI, Security) appear there; PR-only ones need --verify-sha.
develop_sha="$(gh api "repos/$repo/branches/develop" --jq .commit.sha)"
echo "Preflight against develop head $develop_sha:" >&2
if ! preflight="$(verify_on_commit "$develop_sha" 2>&1)"; then
  failing="$(printf '%s\n' "$preflight" | grep -E '^FAIL +(failure|cancelled|timed_out|action_required) ' || true)"
  printf '%s\n' "$preflight" | sed 's/^/  /' >&2
  if [ -n "$failing" ] && ! $force; then
    echo "ABORT: the checks above marked failure are red on develop's head, so this ruleset would" >&2
    echo "block every merge. Fix them first (CodeQL: see ENTERPRISE_SETUP.md step 2, or pass" >&2
    echo "--without-codeql), or pass --force. MISSING lines are PR-only checks: confirm them with" >&2
    echo "--verify-sha <a recent PR head>." >&2
    exit 1
  fi
fi

existing="$(gh api "repos/$repo/rulesets?includes_parents=false&per_page=100" \
  --jq ".[] | select(.name == \"$ruleset_name\" and .source_type == \"Repository\") | .id")"
if [ "$(printf '%s\n' "$existing" | grep -c .)" -gt 1 ]; then
  echo "ABORT: more than one ruleset is named '$ruleset_name' (ids: $existing); delete the extra one" >&2
  exit 1
fi

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
printf '%s\n' "$body" >"$tmp"
if [ -n "$existing" ]; then
  case "$protected_ruleset_ids" in *" $existing "*) echo "ABORT: refusing to modify ruleset $existing" >&2; exit 1 ;; esac
  echo "Updating ruleset $existing ('$ruleset_name')" >&2
  id="$(gh api -X PUT "repos/$repo/rulesets/$existing" --input "$tmp" --jq .id)"
else
  echo "Creating ruleset '$ruleset_name'" >&2
  id="$(gh api -X POST "repos/$repo/rulesets" --input "$tmp" --jq .id)"
fi

echo "Ruleset $id now:" >&2
gh api "repos/$repo/rulesets/$id" \
  --jq '"  enforcement: \(.enforcement)\n  branches: \(.conditions.ref_name.include | join(", "))\n  bypass actors: \(.bypass_actors | length)\n  rules: \([.rules[].type] | join(", "))\n  required checks: \([.rules[] | select(.type == "required_status_checks") | .parameters.required_status_checks[]] | length)"' >&2
echo "Also in force on main: ruleset 19609105 'Main Protected by Ansh' (not modified by this script)." >&2
gh api "repos/$repo/rulesets/19609105" --jq '"  its rules: \([.rules[].type] | join(", ")); bypass actors: \(.bypass_actors | length)"' >&2 || true
