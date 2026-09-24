#!/usr/bin/env bash
# Full local PROTO_SPEC E2E acceptance against the harness synthprov (controller, functional scale).
# Usage: tools/acceptance.sh <outdir>
#  1 unit tests + structural gates        4 base_url-swap app (E3)
#  2 E2E pytest (Python SDK, bytes, output, two tenants, guard kill, disconnect)
#  3 Node SDK matrix                       5 guard-unready-at-startup   6 instrument honesty
set -uo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
OUT=${1:?outdir}; mkdir -p "$OUT"
SYN=$SP/harness/bin/synthprov
PY=$HERE/.venv/bin/python
cd "$HERE"
sha256sum "$SYN" "$SP/harness/bin/olg" "$SP/harness/analyze.py" > "$OUT/harness-sha256.txt"
git -C "$SP/harness" log -1 --format="%H %ad %s" > "$OUT/harness-commit.txt" 2>/dev/null || true
declare -A RES
RV_GUARD_TOKENIZER=$SP/models/Llama-Prompt-Guard-2-22M/tokenizer.json timeout 1500 \
  "$PY" -m pytest tests/unit -q -p no:cacheprovider > "$OUT/1-unit.txt" 2>&1; RES[unit]=$?
"$HERE/.venv/bin/lint-imports" --no-cache >> "$OUT/1-unit.txt" 2>&1; RES[import_linter]=$?
bash tools/local_down.sh
up() {  # $1 = log dir, rest = env assignments for rvproto
  local d=$1; shift
  PROVIDER=synthprov SYNTHPROV_BIN=$SYN \
    SYNTHPROV_ARGS="-listen 127.0.0.1:18080 -record $d/provider-records.jsonl -ttft 50ms -itl 5ms -tokens 20 -stats-out $d/synthprov-stats.json" \
    bash tools/local_up.sh "$d" "$@" > "$d/up.log" 2>&1
}
mkdir -p "$OUT/2-e2e"; up "$OUT/2-e2e"
PROVIDER_KIND=synthprov PROVIDER_RECORDS=$OUT/2-e2e/provider-records.jsonl timeout 900 \
  "$PY" -m pytest tests/e2e -q -p no:cacheprovider -rs --junitxml="$OUT/2-e2e/junit.xml" > "$OUT/2-e2e/pytest.txt" 2>&1
RES[e2e]=$?
docker exec rv-proto-redis redis-cli SET rv:budget:org-q 2500 >/dev/null
(cd tests/e2e/node && timeout 300 node rv_matrix.mjs > "$OUT/3-node-matrix.json" 2> "$OUT/3-node-matrix.err"); RES[node]=$?
OPENAI_BASE_URL=http://127.0.0.1:18080/v1 OPENAI_API_KEY=sk-direct "$PY" tools/app_example.py > "$OUT/4-e3-direct.json" 2>&1
OPENAI_BASE_URL=http://127.0.0.1:8400/v1 OPENAI_API_KEY=sk-rv-org-b-0001 "$PY" tools/app_example.py > "$OUT/4-e3-rvproto.json" 2>&1
"$PY" tools/e3_compare.py "$OUT/4-e3-direct.json" "$OUT/4-e3-rvproto.json" > "$OUT/4-e3-verdict.json"; RES[e3]=$?
bash tools/local_down.sh
mkdir -p "$OUT/5-unready"; up "$OUT/5-unready" RV_GUARD_WARMUP_DELAY_S=25 &
for i in $(seq 1 60); do curl -s -m 1 localhost:8400/healthz | grep -q ok && break; sleep 0.5; done
E2E_UNREADY=1 PROVIDER_KIND=synthprov timeout 120 "$PY" -m pytest \
  tests/e2e/test_e8_cancel_and_unready.py::test_guard_unready_at_startup_is_unavailable_not_clean \
  -q -p no:cacheprovider > "$OUT/5-unready/pytest.txt" 2>&1; RES[unready]=$?
wait; bash tools/local_down.sh
HONESTY_OUT=$OUT/6-honesty bash tools/honesty.sh > "$OUT/6-honesty.log" 2>&1; RES[honesty_runs]=$?
{ echo "{"; for k in "${!RES[@]}"; do echo "  \"$k\": ${RES[$k]},"; done; echo "  \"_\": 0"; echo "}"; } > "$OUT/exit-codes.json"
cat "$OUT/exit-codes.json"
