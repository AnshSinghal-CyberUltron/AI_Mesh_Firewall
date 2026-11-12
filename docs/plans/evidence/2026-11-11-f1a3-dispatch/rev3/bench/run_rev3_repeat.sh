#!/usr/bin/env bash
# F1a-3 plan revision 3: repeats of the head-of-line rows (second heavy-lock acquisition). The first run's
# pipe-100 / flood-1 / pace row (p99 64.9 ms) was an outlier against its flood-4 row (4.6 ms); this run repeats
# the pace and none variants three times at 100 us per event and twice at 0 us, and records the top CPU
# consumers before every row.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
PY=${PY:-/home/claude/work/runs/121-rev2/venv/bin/python}
export PYTHONPATH=${GW:-/home/claude/wt/f1a3-rev2/gateway_v2}
unset HTTPS_PROXY https_proxy HTTP_PROXY http_proxy ALL_PROXY all_proxy NO_PROXY no_proxy
OUT=$HERE/out; LOG=$OUT/run_rev3_repeat.log
snap() { echo "== $1 $(date -u +%FT%TZ) load $(cat /proc/loadavg)"; ps -eo pid,pcpu,etime,comm --sort=-pcpu | head -6; }
cd /tmp
T0=$(date +%s)
echo "queued $(date -u +%FT%TZ) load $(cat /proc/loadavg)" >"$LOG"
exec 9>/home/claude/work/.heavy.lock
flock 9
echo "heavy lock acquired $(date -u +%FT%TZ) after $(( $(date +%s) - T0 )) s; load $(cat /proc/loadavg)" >>"$LOG"
"$PY" "$HERE/hol_pacing_probe.py" server 19372 >"$OUT/hol_repeat.up.log" 2>&1 &
HUP=$!; sleep 1
: >"$OUT/hol_rev3_repeat.jsonl"; : >"$OUT/hol_rev3_repeat.env.txt"
for rep in 1 2 3; do
  for fl in 0 1 4; do for v in none pace; do
    snap "rep${rep}_pipe100_flood${fl}_${v}" >>"$OUT/hol_rev3_repeat.env.txt"
    nice -n 10 "$PY" "$HERE/hol_pacing_probe.py" client 19372 64 "$fl" 100 200 "$v" 131.9 \
      >>"$OUT/hol_rev3_repeat.jsonl" 2>>"$OUT/hol_rev3_repeat.err"
  done; done
  if [ "$rep" -le 2 ]; then for fl in 0 1 4; do
    snap "rep${rep}_pipe0_flood${fl}_pace" >>"$OUT/hol_rev3_repeat.env.txt"
    nice -n 10 "$PY" "$HERE/hol_pacing_probe.py" client 19372 64 "$fl" 0 200 pace 31.9 \
      >>"$OUT/hol_rev3_repeat.jsonl" 2>>"$OUT/hol_rev3_repeat.err"
  done; fi
done
snap end >>"$OUT/hol_rev3_repeat.env.txt"
kill $HUP; wait $HUP 2>/dev/null
echo "released $(date -u +%FT%TZ) load $(cat /proc/loadavg); total $(( $(date +%s) - T0 )) s" >>"$LOG"
flock -u 9
