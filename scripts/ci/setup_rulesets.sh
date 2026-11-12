#!/usr/bin/env bash
# Create or update ONE ruleset that protects develop, staging and main.
# The repository OWNER runs it with an admin token (`gh auth login`); agents never run it.
# docs/ci/ENTERPRISE_SETUP.md says when, and what to decide first (steps 10 and 11).
#
#   scripts/ci/setup_rulesets.sh --dry-run               # print the ruleset JSON; no API call
#   scripts/ci/setup_rulesets.sh --verify-sha <sha>      # read-only: did every required check
#                                                        # report success on that commit?
#   scripts/ci/setup_rulesets.sh --pr-head <sha>         # create or update the ruleset, then
#                                                        # run --check
#   scripts/ci/setup_rulesets.sh --check                 # read-only: is the ruleset in force
#                                                        # as this script writes it?
#   add --org to any of them for an ORGANISATION ruleset that targets this repository only
#
# Pass the same flags to --check as to the apply run: --check compares the live ruleset with the
# body these flags produce, and requires its deletion, non_fast_forward, pull_request and
# required_status_checks rules in `GET repos/{repo}/rules/branches/{develop,staging,main}`.
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
# Safety checks before any write (--force skips the first two):
#   - develop's head: abort if a required check there already failed (CodeQL before step 2), or
#     if its check runs cannot be read: the ruleset would block every merge;
#   - --pr-head SHA (required): every required check must have SUCCEEDED on that PR head. A name
#     that never reports (a renamed job, or applying before PR #137 has merged) would otherwise
#     leave every PR waiting forever. Use the head of a PR opened after #114, #137 and #138 merged;
#   - it never writes ruleset 19609105, and aborts if two rulesets have this one's name.
#
# Organisation ruleset (--org; ENTERPRISE_SETUP.md step 10 recommends it). Agents act through
# the owner's token. Their proxy answers 403 on organisation endpoints, but repository rulesets
# are readable to them (12 Nov 2026), and whether it blocks writes there is unconfirmed. An
# organisation ruleset that targets this repository is out of the agents' reach either way. It
# needs an organisation owner's token; the merge_queue rule is not available at that level.
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
# "Main Protected by Ansh" (id 19609105) is never written by this script. The owner deletes it
# by hand once --check passes (ENTERPRISE_SETUP.md step 11); until then its rules still apply
# to main on top of this ruleset.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall"
checks_file="$here/../../.github/ci/required-checks.txt"
ruleset_name="AMF protected branches: develop, staging, main"
old_ruleset_id=19609105               # "Main Protected by Ansh": read, never written
protected_ruleset_ids=" 19609105 "
approvals="${AMF_REQUIRED_APPROVALS:-0}"
enforcement="active"
dry_run=false strict=true copilot=false merge_queue=false with_codeql=true force=false
check_only=false org_scope=false
verify_sha="" pr_head=""

usage() {
  sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed -e '$d' -e 's/^# \{0,1\}//'
  cat <<'USAGE'
Options:
  --repo OWNER/REPO      default CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall
  --org                  an organisation ruleset that targets OWNER/REPO only (recommended)
  --dry-run              print the ruleset JSON on stdout and exit; makes no API call
  --verify-sha SHA       read-only: report each required check's result on SHA and exit 1
                         unless all succeeded (use a recent PR head: some checks run on PRs only)
  --pr-head SHA          apply: abort unless every required check succeeded on this PR head
  --check                read-only: compare the live ruleset and the three branches' rules with
                         what these flags would write; exit 1 on any difference
  --approvals N          required approving reviews (default: $AMF_REQUIRED_APPROVALS, else 0)
  --no-strict            do not require PR branches to be up to date with the base
  --copilot-review       add the copilot_code_review rule (review on every push, not drafts)
  --merge-queue          add the merge_queue rule (read the header first; not with --org)
  --without-codeql       drop the "CodeQL / ..." checks (CodeQL default setup kept)
  --evaluate             enforcement "evaluate": report what would be blocked, block nothing
  --checks-file PATH     default .github/ci/required-checks.txt
  --force                apply without the develop-head and --pr-head checks
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) repo="${2:?--repo needs OWNER/REPO}"; shift 2 ;;
    --org) org_scope=true; shift ;;
    --dry-run) dry_run=true; shift ;;
    --verify-sha) verify_sha="${2:?--verify-sha needs a commit SHA}"; shift 2 ;;
    --pr-head) pr_head="${2:?--pr-head needs a commit SHA}"; shift 2 ;;
    --check) check_only=true; shift ;;
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
for sha in "$verify_sha" "$pr_head"; do
  case "$sha" in *[!0-9a-f]*) echo "commit SHAs are lowercase hex: '$sha'" >&2; exit 2 ;; esac
done
[ -f "$checks_file" ] || { echo "required checks file not found: $checks_file" >&2; exit 2; }
if $org_scope && $merge_queue; then
  echo "--merge-queue: the merge_queue rule is not available in organisation rulesets" >&2; exit 2
fi
org="${repo%%/*}"
if $org_scope; then
  scope="orgs/$org" source_type="Organization"
else
  scope="repos/$repo" source_type="Repository"
fi

# The ruleset body, built by stdlib Python from the inputs in the environment.
ruleset_json() {
  RS_NAME="$ruleset_name" RS_ENFORCEMENT="$enforcement" RS_APPROVALS="$approvals" \
  RS_STRICT="$strict" RS_COPILOT="$copilot" RS_MERGE_QUEUE="$merge_queue" \
  RS_WITH_CODEQL="$with_codeql" RS_CHECKS_FILE="$checks_file" \
  RS_ORG_REPO="$($org_scope && printf '%s' "${repo#*/}" || true)" python3 -I - <<'PY'
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
conditions = {
    "ref_name": {
        "include": ["refs/heads/develop", "refs/heads/staging", "refs/heads/main"],
        "exclude": [],
    }
}
if os.environ["RS_ORG_REPO"]:
    # This repository only; `protected` stops a rename from moving it out of the ruleset.
    conditions["repository_name"] = {
        "include": [os.environ["RS_ORG_REPO"]],
        "exclude": [],
        "protected": True,
    }
body = {
    "name": os.environ["RS_NAME"],
    "target": "branch",
    "enforcement": os.environ["RS_ENFORCEMENT"],
    "bypass_actors": [],
    "conditions": conditions,
    "rules": rules,
}
print(json.dumps(body, indent=2))
PY
}

# Every successful check run from GitHub Actions on $1, compared with the required names.
# Exit 0: all succeeded; 1: some did not (each listed); 3: the check runs could not be read.
# On an HTTP error `gh` exits non-zero and prints GitHub's error JSON on stdout (it skips --jq),
# so the status is checked and every line must be a check run.
verify_on_commit() {
  local sha="$1" runs
  if ! runs="$(gh api --paginate "repos/$repo/commits/$sha/check-runs?per_page=100" \
      --jq '.check_runs[] | [.id, .name, (.conclusion // .status), (.app.id // 0)] | @tsv')"; then
    echo "cannot read the check runs of $sha: $(printf '%s' "$runs" | head -c 300)" >&2
    return 3
  fi
  RUNS="$runs" BODY="$body" python3 -I - <<'PY'
import json, os, sys

body = json.loads(os.environ["BODY"])
rule = next(r for r in body["rules"] if r["type"] == "required_status_checks")
required = [c["context"] for c in rule["parameters"]["required_status_checks"]]
latest = {}  # name -> (check run id, conclusion or status) of the newest run by GitHub Actions
for line in os.environ["RUNS"].splitlines():
    parts = line.split("\t")
    if len(parts) != 4 or not parts[0].isdigit():
        print(f"unreadable check-run line: {line[:200]!r}", file=sys.stderr)
        sys.exit(3)
    run_id, name, state, app = parts
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

# The id of the ruleset named $ruleset_name in $scope, or nothing. Exit 3 when the list cannot
# be read, 4 when two rulesets have the name.
find_ruleset() {
  local list query="per_page=100"
  $org_scope || query="includes_parents=false&per_page=100"
  if ! list="$(gh api "$scope/rulesets?$query")"; then
    echo "cannot list the rulesets of $scope: $(printf '%s' "$list" | head -c 300)" >&2
    return 3
  fi
  LIST="$list" RS_NAME="$ruleset_name" RS_SOURCE="$source_type" python3 -I - <<'PY'
import json, os, sys

try:
    rulesets = json.loads(os.environ["LIST"])
except ValueError:
    rulesets = None
if not isinstance(rulesets, list):
    print(f"the ruleset list is not a JSON list: {os.environ['LIST'][:200]}", file=sys.stderr)
    sys.exit(3)
ids = [str(r["id"]) for r in rulesets
       if r.get("name") == os.environ["RS_NAME"] and r.get("source_type") == os.environ["RS_SOURCE"]]
if len(ids) > 1:
    print(f"more than one ruleset is named {os.environ['RS_NAME']!r}: {ids}", file=sys.stderr)
    sys.exit(4)
print(ids[0] if ids else "")
PY
}

# Read-only: the live ruleset equals the body these flags produce, and (when active) its core
# rules are in force on develop, staging and main. Prints one line per check; exit 0 when all
# hold, 1 when one does not, 3 when something cannot be read.
check_applied() {
  local id ruleset branch rules all_rules=""
  id="$(find_ruleset)" || return $?
  if [ -z "$id" ]; then
    echo "FAIL  no $source_type ruleset named '$ruleset_name' in $scope"
    return 1
  fi
  if ! ruleset="$(gh api "$scope/rulesets/$id")"; then
    echo "cannot read ruleset $id: $(printf '%s' "$ruleset" | head -c 300)" >&2
    return 3
  fi
  for branch in develop staging main; do
    if ! rules="$(gh api "repos/$repo/rules/branches/$branch?per_page=100")"; then
      echo "cannot read the rules of $branch: $(printf '%s' "$rules" | head -c 300)" >&2
      return 3
    fi
    all_rules="$all_rules$branch	$(printf '%s' "$rules" | tr -d '\n\t')
"
  done
  RS_ID="$id" RULESET="$ruleset" BRANCH_RULES="$all_rules" BODY="$body" \
  OLD_ID="$old_ruleset_id" python3 -I - <<'PY'
import json, os, sys

want = json.loads(os.environ["BODY"])
rs_id = int(os.environ["RS_ID"])

def unreadable(what, raw):
    print(f"{what} is not readable JSON: {raw[:200]}", file=sys.stderr)
    sys.exit(3)

try:
    got = json.loads(os.environ["RULESET"])
except ValueError:
    got = None
if not isinstance(got, dict):
    unreadable("the ruleset", os.environ["RULESET"])
fails = 0

def line(ok, text):
    global fails
    fails += 0 if ok else 1
    print(("ok    " if ok else "FAIL  ") + text)

line(got.get("id") == rs_id and got.get("name") == want["name"], f"ruleset {rs_id} '{got.get('name')}'")
line(got.get("enforcement") == want["enforcement"], f"enforcement {got.get('enforcement')} (want {want['enforcement']})")
line(not got.get("bypass_actors"), f"bypass actors: {len(got.get('bypass_actors') or [])} (want 0)")
line(got.get("target") == "branch", f"target {got.get('target')}")
for key, cond in want["conditions"].items():
    have = (got.get("conditions") or {}).get(key) or {}
    same = sorted(have.get("include", [])) == sorted(cond["include"]) and not have.get("exclude")
    line(same, f"conditions.{key}.include {have.get('include')}")
types = {r["type"]: r for r in got.get("rules", [])}
line(set(types) == {r["type"] for r in want["rules"]}, f"rules: {', '.join(sorted(types))}")
for rule in want["rules"]:
    have = (types.get(rule["type"]) or {}).get("parameters") or {}
    for key, value in (rule.get("parameters") or {}).items():
        if key == "required_status_checks":
            pairs = lambda xs: sorted((c.get("context"), c.get("integration_id")) for c in xs)
            ok = pairs(have.get(key) or []) == pairs(value)
            line(ok, f"{rule['type']}: {len(have.get(key) or [])} required checks, all bound to app 15368 (want {len(value)})")
        elif have.get(key) != value:
            line(False, f"{rule['type']}.{key} = {have.get(key)!r} (want {value!r})")
core = {"deletion", "non_fast_forward", "pull_request", "required_status_checks"}
for row in os.environ["BRANCH_RULES"].splitlines():
    branch, _, raw = row.partition("\t")
    try:
        rules = json.loads(raw)
    except ValueError:
        rules = None
    if not isinstance(rules, list):
        unreadable(f"the rules of {branch}", raw)
    mine = {r.get("type") for r in rules if r.get("ruleset_id") == rs_id}
    others = {}
    for r in rules:
        if r.get("ruleset_id") != rs_id:
            others.setdefault(r.get("ruleset_id"), []).append(r.get("type"))
    if want["enforcement"] == "active":
        line(core <= mine, f"{branch}: in force from ruleset {rs_id}: {', '.join(sorted(mine)) or 'nothing'}")
    else:
        print(f"note  {branch}: GitHub lists only ACTIVE rules for a branch; run without --evaluate, then --check again")
    for other, kinds in sorted(others.items(), key=lambda kv: str(kv[0])):
        print(f"info  {branch}: also in force, ruleset {other}: {', '.join(kinds)}")
print(f"{'OK' if not fails else 'NOT OK'}: {fails} difference(s)")
sys.exit(1 if fails else 0)
PY
}

body="$(ruleset_json)"

if $dry_run; then
  printf '%s\n' "$body"
  exit 0
fi

if ! command -v gh >/dev/null; then echo "gh (GitHub CLI) is required" >&2; exit 2; fi

if [ -n "$verify_sha" ]; then
  echo "Required checks on $verify_sha in $repo:" >&2
  verify_on_commit "$verify_sha"
  exit 0
fi

old_ruleset_status() {
  local summary
  if summary="$(gh api "repos/$repo/rulesets/$old_ruleset_id" \
      --jq '"rules: \([.rules[].type] | join(", ")); enforcement: \(.enforcement)"' 2>/dev/null)"; then
    echo "Ruleset $old_ruleset_id 'Main Protected by Ansh' still applies to main ($summary). This" >&2
    echo "script never modifies it; the owner deletes it once --check is OK (ENTERPRISE_SETUP.md step 11)." >&2
  else
    echo "Ruleset $old_ruleset_id 'Main Protected by Ansh': not readable (deleted, or no access)." >&2
  fi
}

if $check_only; then
  echo "Checking the $source_type ruleset '$ruleset_name' ($scope) and the rules on develop, staging, main:" >&2
  rc=0
  check_applied || rc=$?
  old_ruleset_status
  exit "$rc"
fi

echo "Repository: $repo; ruleset scope: $scope" >&2
for branch in develop staging main; do
  if gh api "repos/$repo/branches/$branch" --silent 2>/dev/null; then
    echo "  branch $branch: exists" >&2
  else
    echo "  branch $branch: MISSING or unreadable; the ruleset applies once it exists" >&2
  fi
done

# Safety check 1: a required check that already failed on develop's head would block every
# merge. Only push-triggered checks (CI, Security) appear there; PR-only ones are MISSING, which
# safety check 2 covers. A failure to read develop's check runs aborts too.
if ! $force; then
  if ! develop_sha="$(gh api "repos/$repo/branches/develop" --jq .commit.sha)"; then
    echo "ABORT: cannot read develop's head: $(printf '%s' "$develop_sha" | head -c 300)" >&2
    exit 1
  fi
  case "$develop_sha" in ''|*[!0-9a-f]*) echo "ABORT: develop's head is not a SHA: $develop_sha" >&2; exit 1 ;; esac
  echo "Safety check 1, develop head $develop_sha:" >&2
  rc=0
  preflight="$(verify_on_commit "$develop_sha" 2>&1)" || rc=$?
  printf '%s\n' "$preflight" | sed 's/^/  /' >&2
  if [ "$rc" -gt 1 ] || ! printf '%s\n' "$preflight" | grep -Eq '^[0-9]+ of [0-9]+ required checks succeeded$'; then
    echo "ABORT: could not read the check runs on develop's head (above), so the ruleset cannot be" >&2
    echo "shown not to block every merge. Fix the token (it needs read access to checks) or pass --force." >&2
    exit 1
  fi
  if printf '%s\n' "$preflight" | grep -Eq '^FAIL +(failure|cancelled|timed_out|action_required|startup_failure|stale) '; then
    echo "ABORT: the checks above marked failure are red on develop's head, so this ruleset would" >&2
    echo "block every merge. Fix them first (CodeQL: see ENTERPRISE_SETUP.md step 2, or pass" >&2
    echo "--without-codeql), or pass --force." >&2
    exit 1
  fi

  # Safety check 2: every required name succeeded on a real PR head, so none is missing.
  if [ -z "$pr_head" ]; then
    echo "ABORT: pass --pr-head <SHA>: the head of a PR opened after #114, #137 and #138 merged, on" >&2
    echo "which every required check succeeded (see --verify-sha). Or --force." >&2
    exit 2
  fi
  echo "Safety check 2, PR head $pr_head:" >&2
  rc=0
  verify_on_commit "$pr_head" >&2 || rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "ABORT: not every required check succeeded on $pr_head (above). A check that never" >&2
    echo "reports would leave every PR waiting. Use another PR head, fix the names, or pass --force." >&2
    exit 1
  fi
fi

existing="$(find_ruleset)"   # errexit: exit 3 (unreadable) or 4 (two rulesets with the name)
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
printf '%s\n' "$body" >"$tmp"
if [ -n "$existing" ]; then
  case "$protected_ruleset_ids" in *" $existing "*) echo "ABORT: refusing to modify ruleset $existing" >&2; exit 1 ;; esac
  echo "Updating ruleset $existing ('$ruleset_name') in $scope" >&2
  write_ok=true
  id="$(gh api -X PUT "$scope/rulesets/$existing" --input "$tmp" --jq .id)" || write_ok=false
else
  echo "Creating ruleset '$ruleset_name' in $scope" >&2
  write_ok=true
  id="$(gh api -X POST "$scope/rulesets" --input "$tmp" --jq .id)" || write_ok=false
fi
if ! $write_ok; then
  echo "ABORT: GitHub refused the write: $(printf '%s' "$id" | head -c 600)" >&2
  exit 1
fi
case "$id" in ''|*[!0-9]*) echo "ABORT: the write did not return a ruleset id: $id" >&2; exit 1 ;; esac

echo "Ruleset $id written. Checking it (the same as --check):" >&2
rc=0
check_applied >&2 || rc=$?
old_ruleset_status
if [ "$rc" -ne 0 ]; then
  echo "The check above is NOT OK: do not delete ruleset $old_ruleset_id until it is." >&2
fi
exit "$rc"
