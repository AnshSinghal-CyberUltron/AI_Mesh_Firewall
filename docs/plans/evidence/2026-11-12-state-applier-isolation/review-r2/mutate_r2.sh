#!/usr/bin/env bash
# Reviewer B (#136) mutation tests on the security decisions. Each mutation edits the worktree,
# runs the PR's three new test files plus the fail-open probe p1 (live harness), then restores.
# Usage: mutate.sh <worktree>
# Author round 2 copy of reviewer B's runs/136-B/harness/mutate.sh: T adds the two new test modules,
# BM8 targets the new KEY applier (quarantine -> evict-only, i.e. the reviewed head).
set -u
WT=$1
cd "$WT/gateway_v2" || exit 1
PY=/home/claude/work/venv-ci/bin/python
T="tests/runtime/test_state_applier_isolation.py tests/edge/test_state_applier_isolation_e2e.py tests/state_control/test_generic_handlers_log_type_only.py tests/runtime/test_state_quarantine.py tests/edge/test_state_quarantine_e2e.py"
run() {
  local name=$1 out probe
  out=$(PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD timeout 900 nice -n 10 $PY -m pytest -q -p no:cacheprovider $T 2>&1 | tail -1)
  probe=$(PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD timeout 300 nice -n 10 $PY /home/claude/work/runs/136-B/harness/fail_open.py p1 2>&1 | grep -o '| [A-Z(][^|]*$')
  echo "$name => pytest: $out || live p1: $probe"
  git -C "$WT" checkout -q -- gateway_v2
  git -C "$WT" status --porcelain gateway_v2 | head -3
}
mut() {  # mut <file> <python-literal old> <python-literal new>
  $PY - "$1" "$2" "$3" <<'EOF'
import sys
p, old, new = sys.argv[1], sys.argv[2], sys.argv[3]
s = open(p).read()
assert s.count(old) == 1, (p, old, s.count(old))
open(p, "w").write(s.replace(old, new))
EOF
}

run "BM0 baseline (no mutation)"

mut gateway_v2/plan/snapshot.py "        if state.reason == PLAN_QUARANTINED:" "        if False and state.reason == PLAN_QUARANTINED:"
run "BM1 snapshot: quarantined tenant falls through to last-known-good inside the window"

mut gateway_v2/plan/store.py "            if held is not None and held.state == QUARANTINED:" "            if False and held is not None and held.state == QUARANTINED:"
run "BM2 PlanStore.read ignores quarantine (the held v1 plan is served)"

mut gateway_v2/plan/delta.py "                self._snapshot.forget(fault.key)" "                pass"
run "BM3 quarantine does not drop the snapshot's last-known-good"

mut gateway_v2/plan/delta.py "            self._store.quarantine(fault.key, feed_seq=fault.feed_seq, error=fault.error)" "            pass"
run "BM4 plan applier ignores faults entirely (cursor still moves past them)"

mut gateway_v2/runtime/state_feed.py "    return record.key == key and record.feed_seq > score and record.feed_seq > manifest.feed_seq" "    return record.key == key and record.feed_seq > score"
run "BM5 in-flight skip without the manifest bound (newer-than-index record inside the manifest is skipped, not faulted)"

mut gateway_v2/runtime/state_feed.py "    return record.key == key and record.feed_seq > score and record.feed_seq > manifest.feed_seq" "    return record.key == key and record.feed_seq != score"
run "BM6 in-flight skip also swallows an OLDER record under a newer score (replay)"

mut gateway_v2/edge/state_runtime.py "ISOLATED_KINDS = frozenset({StateKind.PLAN, StateKind.KEY})" "ISOLATED_KINDS = frozenset({StateKind.PLAN, StateKind.KEY, StateKind.KS, StateKind.BUDGET})"
run "BM7 KS and BUDGET isolated too (their appliers ignore faults: an unreadable switch would read as off)"

mut gateway_v2/edge/state_runtime.py $'                self.identity_cache.quarantine(\n                    ((fault.key, fault.feed_seq) for fault in round_.faults),\n' $'                self.identity_cache.evict(\n                    (fault.key for fault in round_.faults),\n'
run "BM8 a KEY fault only evicts the identity cache (no quarantine: the reviewed head)"
