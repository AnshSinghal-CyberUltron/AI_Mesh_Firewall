#!/usr/bin/env bash
# F1a-3 revision 2 paced benchmark: every scenario under ONE acquisition of the shared heavy lock.
# Harness: Reviewer B's sse_upstream.py + client_cost.py (credited; one added variant) and Reviewer
# A's paced_bench.py (credited, unchanged) for the reconciliation row.
# Usage: VENV=/path/to/venv GW=/path/to/worktree/gateway_v2 ./run_rev2.sh [smoke]
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$HERE/out; mkdir -p "$OUT"
PY=${VENV:?}/bin/python
export PYTHONPATH=${GW:?}
unset HTTPS_PROXY https_proxy HTTP_PROXY http_proxy ALL_PROXY all_proxy NO_PROXY no_proxy
export B121_BENCH_KEY="bench-$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')"
LOG=$OUT/run_rev2.log
V5=httpx_raw,httpx_decode,aiohttp_raw,aiohttp_decode,aiohttp_design

scenario() {  # label port events gap_ms client-args...
  local label=$1 port=$2 events=$3 gap=$4; shift 4
  "$PY" -I "$HERE/sse_upstream.py" --host 127.0.0.1 --port "$port" --events "$events" --gap-ms "$gap" \
     >"$OUT/$label.up.log" 2>&1 &
  local up=$!
  for _ in $(seq 50); do grep -q ready "$OUT/$label.up.log" 2>/dev/null && break; sleep 0.2; done
  { echo "label: $label events=$events gap_ms=$gap args: $*"; echo "start $(date -u +%FT%TZ) load $(cat /proc/loadavg)";
    ps -eo pid,pcpu,etime,args --sort=-pcpu | head -6 | cut -c1-150; } >"$OUT/$label.env.txt"
  nice -n 10 "$PY" "$HERE/client_cost.py" --host 127.0.0.1 --port "$port" --label "$label" "$@" \
     >"$OUT/$label.json" 2>"$OUT/$label.client.err"
  { echo "end $(date -u +%FT%TZ) load $(cat /proc/loadavg)";
    ps -eo pid,pcpu,etime,args --sort=-pcpu | head -6 | cut -c1-150;
    echo "upstream counters: $(curl -s --noproxy '*' http://127.0.0.1:$port/stats)";
    echo "upstream cpu ticks: $(awk '{print $14+$15}' /proc/$up/stat)"; } >>"$OUT/$label.env.txt"
  kill $up 2>/dev/null; wait $up 2>/dev/null
  echo "$label done $(date -u +%FT%TZ)" >>"$LOG"
}

reviewer_a() {  # reconciliation: Reviewer A's lock-step harness, gap 0 and 5 ms, C=32, raw reads
  local port=19069
  for gap in 0 5; do
    "$PY" -I "$HERE/paced_bench_reviewer_a.py" server $port $gap >"$OUT/A_gap${gap}.up.log" 2>&1 &
    local up=$!; sleep 1
    for v in httpx aiohttp; do for rep in 1 2; do
      echo "gap_ms=$gap rep=$rep $(nice -n 10 "$PY" -I "$HERE/paced_bench_reviewer_a.py" client $port $v 600 32)" >>"$OUT/A_reconcile.txt"
    done; done
    kill $up; wait $up 2>/dev/null; port=$((port+1))
  done
}

if [ "${1:-}" = smoke ]; then
  scenario smoke 19060 24 20 --requests 64 --warmup 16 --repeats 1 --concurrency 32 --only $V5,current
  exit 0
fi

T0=$(date +%s)
echo "queued $(date -u +%FT%TZ) load $(cat /proc/loadavg)" >"$LOG"
exec 9>/home/claude/work/.heavy.lock
flock 9   # held for the whole set
echo "heavy lock acquired $(date -u +%FT%TZ) after $(( $(date +%s) - T0 )) s; load $(cat /proc/loadavg)" >>"$LOG"
# P1-P3: same per-worker event rate (C/gap = 3,200 events/s), events per request 24 / 200 / 600
scenario P1_e24_g20_c64   19061 24  20 --requests 512 --warmup 64 --repeats 2 --concurrency 64 --only $V5
scenario P2_e200_g20_c64  19062 200 20 --requests 256 --warmup 64 --repeats 2 --concurrency 64 --only $V5,current
scenario P3_e600_g20_c64  19063 600 20 --requests 128 --warmup 64 --repeats 2 --concurrency 64 --only $V5
# P4-P5: 4x the per-worker event rate (12,800 events/s), the operating point of a busy worker
scenario P4_e200_g20_c256 19064 200 20 --requests 512 --warmup 256 --repeats 2 --concurrency 256 --only $V5
scenario P5_e24_g20_c256  19065 24  20 --requests 1024 --warmup 256 --repeats 2 --concurrency 256 --only $V5
reviewer_a
echo "released $(date -u +%FT%TZ) load $(cat /proc/loadavg); total $(( $(date +%s) - T0 )) s" >>"$LOG"
flock -u 9
