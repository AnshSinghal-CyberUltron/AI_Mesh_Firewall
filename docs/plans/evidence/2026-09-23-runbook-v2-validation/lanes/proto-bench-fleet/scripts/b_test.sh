#!/usr/bin/env bash
# b_test.sh RUN RATE KIND   -- multi-unit correctness under load (GW05/GW06 hooks), client-side RR to the units.
#   KIND=plan_ks : plan push at meas+30 s (org-a a-1 -> NEWVER), org-a kill switch ON at meas+90 s, OFF at meas+105 s
#   KIND=quota   : org-q traffic (key sk-rv-org-q-0001) against a budget set at meas+0 s (BUDGET tokens)
#   All timed writes are made FROM rv-pbf-lg-1 (redis_timed.py) so their instants share lg-1's clock with its
#   request records; per-worker gauges are polled on every unit (gauge_poll.py, 100 ms stat, 1 s dump period).
#   env: UNIT_IPS="10.146.0.2 10.146.0.5 10.146.0.3 10.146.0.4" UNITS (names), NEWVER=a-2, BUDGET, AUTH (auth|auth-q),
#        RAMP=20 WARMUP=30 DURATION=180
set -euo pipefail
source "$(dirname "$0")/env.sh"
export ZONE=asia-northeast1-a
source "$H/deploy/lib.sh"
run=$1 rate=$2 kind=$3
RUNS=${RUNS:-$RAW/runs}
UNIT_IPS=${UNIT_IPS:-"10.146.0.2 10.146.0.5 10.146.0.3 10.146.0.4"}
UNITS=${UNITS:-"rv-pbf-unit-2 rv-pbf-unit-3 rv-pbf-unit-4 rv-pbf-unit-5"}
LGS=${LGS:-"rv-pbf-lg-1 rv-pbf-lg-2 rv-pbf-lg-3"}
PROVS=${PROVS:-"rv-pbf-prov-1 rv-pbf-prov-2"}
RAMP=${RAMP:-20} WARMUP=${WARMUP:-30} DURATION=${DURATION:-180}
AUTH=${AUTH:-auth}
NEWVER=${NEWVER:-a-2}
TARGETS=""; for ip in $UNIT_IPS; do TARGETS+="http://$ip:8400,"; done; TARGETS=${TARGETS%,}
[[ -d "$RUNS/$run" ]] && { log "refusing: $RUNS/$run exists"; exit 1; }
mkdir -p "$RUNS/$run/b"
olgflags="-corpus /home/rv/rv/corpora/headline-22M.jsonl -ramp $RAMP -warmup $WARMUP -duration $DURATION -sse-frac 0.7 -auth-file /home/rv/rv/$AUTH"
cat > "$RUNS/$run/step.json" <<EOF
{"run": "$run", "rate": $rate, "mode": "sut", "kind": "$kind", "corpus": "headline-22M.jsonl", "targets": "$TARGETS",
 "units": "$UNITS", "auth": "$AUTH", "olg_flags": "$olgflags", "lgs": "$LGS", "provs": "$PROVS", "sut_vms": "",
 "newver": "$NEWVER", "budget": "${BUDGET:-}", "region_zone": "asia-northeast1-a", "created_utc": "$(date -u +%FT%TZ)"}
EOF
for p in $PROVS; do "$H/deploy/prov_start.sh" "$p" "$run" -ttft 150ms -itl 20ms > /dev/null & done; wait
k=$(wc -w <<<"$LGS")
lgrate=$(python3 -c "print($rate/$k)")
start=$(python3 -c "import time; print(int(time.time()*1000) + 25000)")
meas=$(python3 -c "print($start/1000 + $RAMP + $WARMUP)")
total=$((RAMP + WARMUP + DURATION + 60))
for ip in $UNIT_IPS; do
  ssh "${SSH_OPTS[@]}" rv@$ip "nohup python3 ~/rv/gauge_poll.py /dev/shm/gp-$run.jsonl $((total + 30)) > /dev/null 2>&1 < /dev/null &" &
done; wait
if [[ $kind == plan_ks ]]; then
  # validated plan document for NEWVER (compiled by the image's own compiler on the first unit)
  ssh "${SSH_OPTS[@]}" rv@$(echo $UNIT_IPS | awk '{print $1}') "cd ~/rv/rvproto && ~/rv/venv/bin/python -c \"
import json, sys
sys.path.insert(0, '.')
from rvproto.plan.fixtures import org_a
from rvproto.plan.compiler import compile_plan
d = org_a('$NEWVER'); compile_plan(d); print(json.dumps(d))\"" > "$RUNS/$run/b/plan-$NEWVER.json"
  rscp_to rv-pbf-lg-1 /home/rv/rv/plan-$NEWVER.json "$RUNS/$run/b/plan-$NEWVER.json"
  rssh rv-pbf-lg-1 "nohup bash -c 'python3 -c \"import time; time.sleep(max(0, $meas + 30 - time.time()))\"; \
    python3 ~/rv/redis_timed.py 10.146.0.13:6379 plan org-a $NEWVER ~/rv/plan-$NEWVER.json > ~/rv/b-$run-plan.json; \
    python3 -c \"import time; time.sleep(max(0, $meas + 90 - time.time()))\"; \
    python3 ~/rv/redis_timed.py 10.146.0.13:6379 ks-org org-a on > ~/rv/b-$run-ks-on.json; \
    python3 -c \"import time; time.sleep(max(0, $meas + 105 - time.time()))\"; \
    python3 ~/rv/redis_timed.py 10.146.0.13:6379 ks-org org-a off > ~/rv/b-$run-ks-off.json' > /dev/null 2>&1 < /dev/null &"
elif [[ $kind == quota ]]; then
  rssh rv-pbf-lg-1 "nohup bash -c 'python3 -c \"import time; time.sleep(max(0, $start/1000 - 3 - time.time()))\"; \
    python3 ~/rv/redis_timed.py 10.146.0.13:6379 budget org-q ${BUDGET:?} > ~/rv/b-$run-budget.json' > /dev/null 2>&1 < /dev/null &"
  # budget is written 3 s BEFORE the first request (quota runs start from freshly restarted units)

fi
i=0
for n in $LGS; do
  rssh "$n" "set -e; d=~/rv/runs/$run/lg; rm -rf \$d; mkdir -p \$d; cd \$d; cp ~/rv/hostinfo.txt .; \
    nohup ~/rv/bin/olg -targets $TARGETS -rate $lgrate -lg $i -lg-count $k -start-at $start -out \$d -run-id $run $olgflags \
    > olg.log 2>&1 < /dev/null & echo \$! > olg.pid" &
  i=$((i+1))
done
wait
log "$run: $kind olg started (rate $lgrate each x $k, start-at $start, meas $meas, targets $TARGETS)"
sleep $((RAMP + WARMUP + DURATION + 25))
for n in $LGS; do
  while rssh "$n" "kill -0 \$(cat ~/rv/runs/$run/lg/olg.pid) 2>/dev/null"; do sleep 5; done
  rssh "$n" "tail -1 ~/rv/runs/$run/lg/olg.log" | sed "s/^/[$n] /"
done
for p in $PROVS; do "$H/deploy/prov_stop.sh" "$p" "$run" > /dev/null & done; wait
sleep 5
for ip in $UNIT_IPS; do
  (scp -q "${SSH_OPTS[@]}" rv@$ip:/dev/shm/gp-$run.jsonl "$RUNS/$run/b/gp-$ip.jsonl"; \
   ssh "${SSH_OPTS[@]}" rv@$ip "curl -s -m 10 localhost:8400/metrics/all" > "$RUNS/$run/b/metrics-all-$ip.json") &
done; wait
scp -q "${SSH_OPTS[@]}" "rv@$(vm_ip rv-pbf-lg-1):rv/b-$run-*.json" "$RUNS/$run/b/" || true
"$H/deploy/collect.sh" "$run" "$RUNS/$run" $LGS $PROVS 2>/dev/null
echo "$run collected"
