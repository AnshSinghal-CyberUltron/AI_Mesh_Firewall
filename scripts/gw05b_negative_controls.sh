#!/usr/bin/env bash
# GW05b — the negative controls, as a reproducible run rather than something done once in a shell.
#
# Each control removes ONE clause of the fix and requires the paired test file to FAIL. A state
# test that cannot fail is worse than no test, and this programme has the receipts: CHG-0009
# found an oracle whose loop was `for fs in []` (always zero, never gated), CHG-0012 found a
# redaction check that never looked at the response bytes, and CHG-0029 found a leak oracle
# matching one hardcoded SSN. All three passed continuously while proving nothing.
#
# A caching note that cost real time: swapping two adjacent lines leaves the file BYTE-IDENTICAL
# in size, and if the edit and the restore land in the same second, Python's (mtime, size) pyc
# invalidation keeps serving the stale bytecode. Every mutation here purges __pycache__.
#
#   usage: scripts/gw05b_negative_controls.sh
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="${REPO}/gateway_v2"
VENV="${WORKSPACE}/.venv/bin"
BACKUP="$(mktemp -d)"
FAILURES=0

cd "${WORKSPACE}"

purge() {
  find . -name "__pycache__" -type d -not -path "./.venv/*" -exec rm -rf {} + 2>/dev/null || true
}

restore_all() {
  for saved in "${BACKUP}"/*.bak; do
    [ -e "${saved}" ] || continue
    target="$(basename "${saved}" .bak | tr '%' '/')"
    cp "${BACKUP}/$(basename "${saved}")" "${WORKSPACE}/${target}"
  done
  purge
  rm -rf "${BACKUP}"
}
trap restore_all EXIT

save() {
  cp "${WORKSPACE}/$1" "${BACKUP}/$(echo "$1" | tr '/' '%').bak"
}

# control <label> <file> <test-path> <python-literal-old> <python-literal-new>
control() {
  local label="$1" file="$2" tests="$3" old="$4" new="$5"
  save "${file}"
  OLD="${old}" NEW="${new}" "${VENV}/python" - "${WORKSPACE}/${file}" <<'PY'
import os, pathlib, sys
p = pathlib.Path(sys.argv[1])
t = p.read_text()
old, new = os.environ["OLD"], os.environ["NEW"]
if t.count(old) != 1:
    sys.exit(f"control anchor matched {t.count(old)} times, expected 1")
p.write_text(t.replace(old, new))
PY
  if [ $? -ne 0 ]; then
    echo "  ANCHOR LOST  ${label} -- the control no longer matches the code"
    FAILURES=$((FAILURES + 1))
    cp "${BACKUP}/$(echo "${file}" | tr '/' '%').bak" "${WORKSPACE}/${file}"
    purge
    return
  fi
  purge
  local out
  out="$("${VENV}/python" -m pytest ${tests} -q -p no:cacheprovider 2>&1 | tail -1)"
  cp "${BACKUP}/$(echo "${file}" | tr '/' '%').bak" "${WORKSPACE}/${file}"
  purge
  if echo "${out}" | grep -q "failed"; then
    echo "  bites       ${label}  ->  ${out}"
  else
    echo "  NO EFFECT   ${label}  ->  ${out}"
    FAILURES=$((FAILURES + 1))
  fi
}

echo "==> baseline (every suite must pass before any control is applied)"
purge
BASELINE="$("${VENV}/python" -m pytest -q -p no:cacheprovider 2>&1 | tail -1)"
echo "  ${BASELINE}"
if echo "${BASELINE}" | grep -q "failed"; then
  echo "  baseline is not green; controls would be meaningless"
  exit 1
fi

echo "==> phase 3: the floor"
control "the floor is never passed to poll" \
  "gateway_v2/runtime/state_task.py" \
  "tests/runtime/test_lgw05b_stamp_view.py tests/runtime/test_lgw05c_sync.py" \
  "                kind, cursor, limit=self._budget.records, floor=self.floor(kind)," \
  "                kind, cursor, limit=self._budget.records,"

control "the floor is collapsed back into the cursor" \
  "gateway_v2/runtime/state_task.py" \
  "tests/runtime/test_lgw05b_stamp_view.py" \
  "        self._floors[kind] = floor" \
  "        self._floors[kind] = floor
        self._cursors[kind] = floor"

control "the valkey adapter never finds the stamp" \
  "gateway_v2/runtime/store_valkey.py" \
  "tests/runtime/test_lgw05b_stamp_view.py" \
  "        return _as_bytes(await self._client.get(self._keys.stamp))" \
  "        return None"

echo "==> phase 4: the per-kind posture"
control "a cached principal is served while unverified" \
  "gateway_v2/admit/identity.py" \
  "tests/admit/test_lgw05b_failclosed.py" \
  "        if self.unverified() is not None:
            return None
        held = self._held.get(key_hash)" \
  "        held = self._held.get(key_hash)"

control "the kill switch ignores the stamp" \
  "gateway_v2/admit/killswitch.py" \
  "tests/admit/test_lgw05b_failclosed.py" \
  "        if self._stamp is not None and not self._stamp.fresh():
            return KillSwitchState.STALE
        return KillSwitchState.ENGAGED if engaged else KillSwitchState.OK" \
  "        return KillSwitchState.ENGAGED if engaged else KillSwitchState.OK"

control "a plan is served while unverified" \
  "gateway_v2/plan/snapshot.py" \
  "tests/admit/test_lgw05b_failclosed.py" \
  "        unverified = self._unverified()
        if unverified is not None:" \
  "        unverified = None
        if unverified is not None:"

echo "==> phase 5: the postgres ride-through"
control "the ride-through never applies" \
  "state_control/rehydrate.py" \
  "tests/state_control/test_lgw05b_pg_grace.py" \
  "        ride = self._ride_through(verified_at)" \
  "        ride = None"

control "the window is anchored on the last WRITTEN stamp" \
  "state_control/rehydrate.py" \
  "tests/state_control/test_lgw05b_pg_grace.py" \
  "        anchor = self._verified
        if anchor is None:" \
  "        anchor = self._stamped
        if anchor is None:"

control "a store that lost data still rides through" \
  "state_control/rehydrate.py" \
  "tests/state_control/test_lgw05b_pg_grace.py" \
  "        if not self._store_holds(anchor.cursors):
            return None" \
  "        if False:
            return None"

echo "==> phase 2: honest silence"
control "a partial round stamps the kinds that worked" \
  "state_control/rehydrate.py" \
  "tests/state_control/test_lgw05b_rehydrate_stamp.py" \
  "        missing = sorted(kind.value for kind in StateKind if kind not in verified)
        if not missing:
            return dict(verified), False" \
  "        missing: list[str] = []
        if not missing:
            return dict(verified), False"

echo "==> phase 6: cadence and logging"
control "the recheck cadence is flattened" \
  "gateway_v2/runtime/state_stamp.py" \
  "tests/runtime/test_lgw05b_cadence_metrics.py" \
  "        if not store_answered or self.fresh():
            return period_s
        return min(period_s, RECHECK_S)" \
  "        return period_s"

control "freshness is logged on every call, not on transitions" \
  "gateway_v2/runtime/state_stamp.py" \
  "tests/runtime/test_lgw05b_cadence_metrics.py" \
  "            if fresh == previous:
                return None" \
  "            if False:
                return None"

echo "==> phase 1: the start gate"
control "a pre-start stamp is treated as fresh (I3 removed)" \
  "gateway_v2/runtime/state_stamp.py" \
  "tests/runtime/test_lgw05b_stamp_view.py tests/admit/test_lgw05b_failclosed.py" \
  "            if verified is None or verified < self._started_at:
                return False" \
  "            if verified is None:
                return False"

control "a stamp need not cover every kind" \
  "gateway_v2/runtime/state_sig.py" \
  "tests/runtime/test_lgw05b_stamp_sig.py" \
  "    missing = sorted(kind.value for kind in StateKind if kind not in out)
    if missing:" \
  "    missing: list[str] = []
    if missing:"

echo
if [ "${FAILURES}" -eq 0 ]; then
  echo "==> all controls bite: every clause of the fix is load-bearing"
else
  echo "==> ${FAILURES} control(s) had NO EFFECT or lost their anchor -- investigate"
  exit 1
fi
