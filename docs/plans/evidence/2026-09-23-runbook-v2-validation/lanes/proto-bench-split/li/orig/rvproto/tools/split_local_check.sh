#!/usr/bin/env bash
# Split topology (guard-only node + gateway-only node) on loopback TCP, against the harness
# synthprov: the full E2E suite (incl. guard kill hook, owner process kill + restart, owner-queue
# shed, provider-byte proofs), then guard-unready-at-startup. Usage: tools/split_local_check.sh <outdir>
set -uo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
OUT=${1:?outdir}; mkdir -p "$OUT"
SYN=$SP/harness/bin/synthprov
PY=$HERE/.venv/bin/python
cd "$HERE"
sha256sum "$SYN" > "$OUT/harness-sha256.txt"
declare -A RES
bash tools/local_down.sh
up() {  # $1 = log dir, rest = env assignments
  local d=$1; shift
  GUARD_NODE=1 PROVIDER=synthprov SYNTHPROV_BIN=$SYN \
    SYNTHPROV_ARGS="-listen 127.0.0.1:18080 -record $d/provider-records.jsonl -ttft 50ms -itl 5ms -tokens 20 -stats-out $d/synthprov-stats.json" \
    bash tools/local_up.sh "$d" "$@" > "$d/up.log" 2>&1
}
mkdir -p "$OUT/e2e"; up "$OUT/e2e"
curl -s localhost:8400/readyz > "$OUT/e2e/readyz-after-up.json"
PROVIDER_KIND=synthprov PROVIDER_RECORDS=$OUT/e2e/provider-records.jsonl timeout 900 \
  "$PY" -m pytest tests/e2e -q -p no:cacheprovider -rs -s --junitxml="$OUT/e2e/junit.xml" > "$OUT/e2e/pytest.txt" 2>&1
RES[e2e]=$?
curl -s localhost:8400/metrics/all > "$OUT/e2e/metrics-all.json"
bash tools/local_down.sh
mkdir -p "$OUT/unready"; up "$OUT/unready" RV_GUARD_WARMUP_DELAY_S=25 &
for i in $(seq 1 60); do curl -s -m 1 localhost:8400/healthz | grep -q ok && break; sleep 0.5; done
curl -s localhost:8400/readyz > "$OUT/unready/readyz-during-warmup.json"
E2E_UNREADY=1 PROVIDER_KIND=synthprov timeout 120 "$PY" -m pytest \
  tests/e2e/test_e8_cancel_and_unready.py::test_guard_unready_at_startup_is_unavailable_not_clean \
  -q -p no:cacheprovider > "$OUT/unready/pytest.txt" 2>&1; RES[unready]=$?
wait; bash tools/local_down.sh
{ echo "{"; for k in "${!RES[@]}"; do echo "  \"$k\": ${RES[$k]},"; done; echo "  \"_\": 0"; echo "}"; } > "$OUT/exit-codes.json"
cat "$OUT/exit-codes.json"
