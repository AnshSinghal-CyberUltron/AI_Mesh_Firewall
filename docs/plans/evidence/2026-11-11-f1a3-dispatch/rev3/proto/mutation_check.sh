#!/usr/bin/env bash
# Mutation check for the stream-slot prototype: each mutant reverts one revision-3 rule, and the test that
# pins the rule must FAIL. Usage: PY=<python with pytest> GW=<worktree>/gateway_v2 ./mutation_check.sh
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
PY=${PY:-python3}
GW=${GW:?set GW to <worktree>/gateway_v2}
mutate() {  # name, file, python-replacement-old, python-replacement-new, test selector
  local d; d=$(mktemp -d)
  cp "$HERE"/*.py "$d"/
  "$PY" - "$d/$2" "$3" "$4" <<'EOF'
import sys
p, old, new = sys.argv[1], sys.argv[2], sys.argv[3]
s = open(p).read()
assert s.count(old) == 1, (p, old)
open(p, "w").write(s.replace(old, new))
EOF
  (cd /tmp && PYTHONPATH="$GW:$d" "$PY" -m pytest -q -p no:cacheprovider "$d/check_stream_slots_proto.py" -k "$5" 2>&1 | grep -E "[0-9]+ (passed|failed)" | tail -1) \
    | sed "s#^#$1: #"
  rm -rf "$d"
}
mutate "M1 hand-off without re-registering the drain record (revision 2)" stream_slots_proto.py \
  $'        self.c.complete(request_id)\n        self.c.register_stream(request_id)' $'        self.c.complete(request_id)' \
  visible_to_the_drain
mutate "M2 no release on cancel-after-grant" stream_slots_proto.py \
  'if fut.done() and not fut.cancelled() and fut.exception() is None:' 'if False:' cancel_after_grant
mutate "M3 cancel without the grant loop" slots_core.py \
  $'                self.n_waiting -= 1\n                return self._grant_waiters()' $'                self.n_waiting -= 1\n                return []' \
  random_10k
mutate "M4 no per-org wait cap" slots_core.py \
  '(q is not None and len(q) >= self.limits.wait_per_org) or ' '' waiters_do_not_hold
mutate "M5 no reserve rule" slots_core.py \
  'if self.held_slots.get(org, 0) < lim.reserve:  # small holder: may use the reserve' 'if True:' \
  idle_worker_serves_one_org_up_to
mutate "M6 release to the oldest waiter instead of the smallest share" slots_core.py \
  'w = min(heads, key=lambda h: (self.share(h.org), h.seq))' 'w = min(heads, key=lambda h: h.seq)' \
  smallest_dominant_share
