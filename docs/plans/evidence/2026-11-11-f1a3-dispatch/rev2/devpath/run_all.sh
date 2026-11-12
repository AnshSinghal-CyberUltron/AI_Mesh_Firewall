#!/usr/bin/env bash
# devpath set under one heavy-lock acquisition: develop dev-profile full path (D1, D2) and the
# dispatch-only variants at the SAME single-stream condition (C=1 effective: C4 serialises the
# dev provider to one upstream connection per worker), for the pipeline/dispatch split.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); OUT=$HERE/out; mkdir -p "$OUT"; LOG=$OUT/run_all.log
export GWPY=/home/claude/work/venv-ci/bin/python BENCHPY=/home/claude/work/runs/121-rev2/venv/bin/python GW=/home/claude/wt/f1a3-rev2/gateway_v2
T0=$(date +%s); echo "queued $(date -u +%FT%TZ) load $(cat /proc/loadavg)" >"$LOG"
exec 9>/home/claude/work/.heavy.lock; flock 9
echo "heavy lock acquired $(date -u +%FT%TZ) after $(( $(date +%s) - T0 )) s; load $(cat /proc/loadavg)" >>"$LOG"
"$HERE/run.sh" D1_e24_g20 24 20 96 4 >>"$LOG" 2>&1
"$HERE/run.sh" D2_e200_g20 200 20 24 4 >>"$LOG" 2>&1
unset HTTPS_PROXY https_proxy HTTP_PROXY http_proxy ALL_PROXY all_proxy NO_PROXY no_proxy
export PYTHONPATH=$GW B121_BENCH_KEY="bench-$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')"
for spec in "24 96" "200 24"; do
  set -- $spec; E=$1; N=$2; PORT=$((19190 + E % 7))
  "$BENCHPY" -I "$HERE/../bench/sse_upstream.py" --host 127.0.0.1 --port $PORT --events $E --gap-ms 20 >"$OUT/C1_e$E.up.log" 2>&1 &
  UP=$!; sleep 1.5
  nice -n 10 "$BENCHPY" "$HERE/../bench/client_cost.py" --host 127.0.0.1 --port $PORT --label C1_e$E \
     --requests $N --warmup 8 --repeats 1 --concurrency 1 --only current,aiohttp_design \
     >"$OUT/C1_e$E.json" 2>"$OUT/C1_e$E.client.err"
  kill $UP; wait $UP 2>/dev/null
done
echo "released $(date -u +%FT%TZ) load $(cat /proc/loadavg); total $(( $(date +%s) - T0 )) s" >>"$LOG"
