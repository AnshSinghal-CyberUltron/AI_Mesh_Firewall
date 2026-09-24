#!/usr/bin/env bash
# olg_run.sh RUN TOTAL_RATE TARGETS "OLG_FLAGS" LGNAME [LGNAME...]
#   Runs olg on every loadgen VM with a synchronized wall-clock start (-start-at now+20s), rate split
#   evenly (-rate TOTAL/K, -lg i -lg-count K), output in ~/rv/runs/RUN/lg; blocks until all finish.
#   OLG_FLAGS must include -corpus ~/rv/corpora/<file> and -duration/-warmup/-ramp as needed.
source "$(dirname "$0")/lib.sh"
run=$1 total=$2 targets=$3 flags=$4; shift 4
k=$#
rate=$(python3 -c "print($total/$k)")
start=$(python3 -c "import time; print(int(time.time()*1000) + ${START_DELAY_S:-20}*1000)")  # date +%3N is not portable
i=0
for n in "$@"; do
  rssh "$n" "set -e; d=~/rv/runs/$run/lg; rm -rf \$d; mkdir -p \$d; cd \$d; cp ~/rv/hostinfo.txt .; \
    nohup ~/rv/bin/olg -targets $targets -rate $rate -lg $i -lg-count $k -start-at $start -out \$d -run-id $run $flags \
    > olg.log 2>&1 < /dev/null & echo \$! > olg.pid" &
  i=$((i+1))
done
wait
log "olg started on $* (rate $rate each, start-at $start)"
for n in "$@"; do
  while rssh "$n" "kill -0 \$(cat ~/rv/runs/$run/lg/olg.pid) 2>/dev/null"; do sleep 10; done
  rssh "$n" "tail -1 ~/rv/runs/$run/lg/olg.log" | sed "s/^/[$n] /"
done
