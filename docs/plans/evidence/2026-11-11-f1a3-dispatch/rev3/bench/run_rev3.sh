#!/usr/bin/env bash
# F1a-3 plan revision 3: every CPU-sensitive measurement under ONE acquisition of the shared heavy lock.
#  A. head-of-line probe (Reviewer B's hol_probe.py + the rev-3 read discipline): 64 paced streams + 0/1/4
#     unpaced streams, per-event pipeline work 0 / 100 us, variants none (B's client) / yield / pace
#  B. per-stream memory bound: hostile shapes U/E/S x read_bufsize 16/64 KiB, 64 streams, 256 KiB event bound,
#     SO_RCVBUF 64 KiB (and autotuned, for S); retained request state through develop's parser
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
PY=${PY:-/home/claude/work/runs/121-rev2/venv/bin/python}
export PYTHONPATH=${GW:-/home/claude/wt/f1a3-rev2/gateway_v2}
unset HTTPS_PROXY https_proxy HTTP_PROXY http_proxy ALL_PROXY all_proxy NO_PROXY no_proxy
OUT=$HERE/out; POUT=$ROOT/probes/out; LOG=$OUT/run_rev3.log
mkdir -p "$OUT" "$POUT"
snap() { echo "$1 $(date -u +%FT%TZ) load $(cat /proc/loadavg)"; ps -eo pid,pcpu,etime,comm --sort=-pcpu | head -6; }
cd /tmp  # neutral cwd: nothing importable next to the interpreter

T0=$(date +%s)
echo "queued $(date -u +%FT%TZ) load $(cat /proc/loadavg)" >"$LOG"
exec 9>/home/claude/work/.heavy.lock
flock 9
echo "heavy lock acquired $(date -u +%FT%TZ) after $(( $(date +%s) - T0 )) s; load $(cat /proc/loadavg)" >>"$LOG"
"$PY" -c "import sys,aiohttp,uvloop,gateway_v2;print(sys.version.split()[0],aiohttp.__version__,uvloop.__version__,gateway_v2.__file__)" >>"$LOG"

# A. head-of-line, with and without the rev-3 read discipline
"$PY" "$HERE/hol_pacing_probe.py" server 19370 >"$OUT/hol.up.log" 2>&1 &
HUP=$!; sleep 1
: >"$OUT/hol_rev3.jsonl"; : >"$OUT/hol_rev3.env.txt"
for pipe in 0 100; do
  cev=$([ "$pipe" = 0 ] && echo 31.9 || echo 131.9)
  for fl in 0 1 4; do for v in none yield pace; do
    snap "hol_pipe${pipe}_flood${fl}_${v}" | head -1 >>"$OUT/hol_rev3.env.txt"
    nice -n 10 "$PY" "$HERE/hol_pacing_probe.py" client 19370 64 "$fl" "$pipe" 200 "$v" "$cev" \
      >>"$OUT/hol_rev3.jsonl" 2>>"$OUT/hol_rev3.err"
  done; done
done
snap hol_end >>"$OUT/hol_rev3.env.txt"
kill $HUP; wait $HUP 2>/dev/null
echo "hol done $(date -u +%FT%TZ)" >>"$LOG"

# B. memory bound
"$PY" "$ROOT/probes/stream_memory_bound.py" server 19371 >"$POUT/smb.up.log" 2>&1 &
MUP=$!; sleep 1
: >"$POUT/hostile.jsonl"
for rb in 16384 65536; do for sh in U E S; do
  nice -n 10 "$PY" "$ROOT/probes/stream_memory_bound.py" hostile 19371 "$sh" "$rb" 64 262144 65536 \
    >>"$POUT/hostile.jsonl" 2>>"$POUT/hostile.err"
done; done
nice -n 10 "$PY" "$ROOT/probes/stream_memory_bound.py" hostile 19371 S 16384 64 262144 0 \
  >>"$POUT/hostile.jsonl" 2>>"$POUT/hostile.err"
kill $MUP; wait $MUP 2>/dev/null
nice -n 10 "$PY" "$ROOT/probes/stream_memory_bound.py" retained >"$POUT/retained.jsonl" 2>>"$POUT/retained.err"
snap mem_end >>"$OUT/hol_rev3.env.txt"
echo "memory done $(date -u +%FT%TZ)" >>"$LOG"
echo "released $(date -u +%FT%TZ) load $(cat /proc/loadavg); total $(( $(date +%s) - T0 )) s" >>"$LOG"
flock -u 9
