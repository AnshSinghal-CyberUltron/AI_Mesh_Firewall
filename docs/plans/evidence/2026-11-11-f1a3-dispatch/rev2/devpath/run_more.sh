#!/usr/bin/env bash
# Second devpath set (one heavy-lock acquisition): D2 re-run with a clean idle baseline (the first
# D2's warm-up hit a gateway still booting, so its "idle" window contained boot CPU), and D3 at
# 600 events, to characterise how the develop pipeline's cost grows with events per request.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); OUT=$HERE/out; mkdir -p "$OUT"; LOG=$OUT/run_more.log
export GWPY=/home/claude/work/venv-ci/bin/python BENCHPY=/home/claude/work/runs/121-rev2/venv/bin/python GW=/home/claude/wt/f1a3-rev2/gateway_v2
T0=$(date +%s); echo "queued $(date -u +%FT%TZ) load $(cat /proc/loadavg)" >"$LOG"
exec 9>/home/claude/work/.heavy.lock; flock 9
echo "heavy lock acquired $(date -u +%FT%TZ) after $(( $(date +%s) - T0 )) s; load $(cat /proc/loadavg)" >>"$LOG"
"$HERE/run.sh" D2b_e200_g20 200 20 24 4 >>"$LOG" 2>&1
"$HERE/run.sh" D3_e600_g20 600 20 8 4 >>"$LOG" 2>&1
echo "released $(date -u +%FT%TZ) load $(cat /proc/loadavg); total $(( $(date +%s) - T0 )) s" >>"$LOG"
