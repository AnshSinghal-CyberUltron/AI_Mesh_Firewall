#!/usr/bin/env bash
# pbf_step.sh RUN TOTAL_RATE
#   One open-loop measurement step for the proto-bench-fleet lane (same sequence as harness
#   deploy/step.sh and proto-bench-unit pbu_step.sh: fresh synthprov, nstat/netdev before/after,
#   synchronized olg start on every loadgen, collect, analyze) plus a SUT-side sampler on every
#   unit under test, on the nginx edge and on the shared-Redis VM, all scheduled for the same
#   wall-clock measurement window (metrics/iptables/INFO snapshots at meas_start and meas_end).
#   env:
#     UNITS="rv-pbf-unit-2 ..."   units under test (sampled; their metrics deltas are summed)
#     TARGETS                      default http://<edge>:8080 (EDGE=1); client-side RR if set to units
#     LGS="rv-pbf-lg-1 rv-pbf-lg-2 rv-pbf-lg-3"   PROVS="rv-pbf-prov-1 rv-pbf-prov-2"
#     RAMP=30 WARMUP=60 DURATION=300 SSE_FRAC=0.7 CORPUS=headline-22M.jsonl AUTH=auth
#     PROV_FLAGS="-ttft 150ms -itl 20ms" OLG_EXTRA="" LABEL="" MODE=sut|direct
#     PROBE_UNIT (unit that PINGs Redis during the window; default first of UNITS)
set -euo pipefail
source "$(dirname "$0")/env.sh"
export ZONE=asia-northeast1-a
source "$H/deploy/lib.sh"
run=$1 rate=$2
RUNS=${RUNS:-$RAW/runs}
MODE=${MODE:-sut}
UNITS=${UNITS:-rv-pbf-unit-2}
LGS=${LGS:-"rv-pbf-lg-1 rv-pbf-lg-2 rv-pbf-lg-3"}
PROVS=${PROVS:-"rv-pbf-prov-1 rv-pbf-prov-2"}
EDGE_VM=${EDGE_VM:-rv-pbf-edge-1}
REDIS_VM=${REDIS_VM:-rv-pbf-redis-1}
RAMP=${RAMP:-30} WARMUP=${WARMUP:-60} DURATION=${DURATION:-300} SSE_FRAC=${SSE_FRAC:-0.7}
CORPUS=${CORPUS:-headline-22M.jsonl}
AUTH=${AUTH:-auth}
PROV_FLAGS=${PROV_FLAGS:-"-ttft 150ms -itl 20ms -sample-mod ${SAMPLE_MOD:-1}"}
PROFILE_STAGES=${PROFILE_STAGES:-canon,det,sem,resolve,dispatch,out,audit}
[[ -d "$RUNS/$run" ]] && { log "refusing: $RUNS/$run exists"; exit 1; }
mkdir -p "$RUNS/$run"
sut_vms=""
if [[ $MODE == direct ]]; then
  TARGETS=""
  for p in $PROVS; do ip=$(vm_ip "$p"); for port in 8080 8081 8082 8083; do TARGETS+="http://$ip:$port,"; done; done
  TARGETS=${TARGETS%,}
else
  TARGETS=${TARGETS:-http://$(vm_ip $EDGE_VM):8080}
  sut_vms="$UNITS $REDIS_VM"
  [[ $TARGETS == *"$(vm_ip $EDGE_VM)"* ]] && sut_vms="$sut_vms $EDGE_VM"
fi
PROBE_UNIT=${PROBE_UNIT:-$(echo $UNITS | awk '{print $1}')}
authflag=""; [[ $AUTH != none ]] && authflag="-auth-file /home/rv/rv/$AUTH"
olgflags="-corpus /home/rv/rv/corpora/$CORPUS -ramp $RAMP -warmup $WARMUP -duration $DURATION -sse-frac $SSE_FRAC -sample-mod ${SAMPLE_MOD:-1} $authflag ${OLG_EXTRA:-}"
cat > "$RUNS/$run/step.json" <<EOF
{"run": "$run", "rate": $rate, "mode": "$MODE", "corpus": "$CORPUS", "targets": "$TARGETS", "units": "$UNITS",
 "auth": "$AUTH", "prov_flags": "$PROV_FLAGS", "olg_flags": "$olgflags", "lgs": "$LGS", "provs": "$PROVS",
 "sut_vms": "$sut_vms", "label": "${LABEL:-}", "region_zone": "asia-northeast1-a", "created_utc": "$(date -u +%FT%TZ)"}
EOF
for v in $LGS $PROVS $sut_vms; do rssh "$v" "mkdir -p ~/rv/runs/$run && nstat -az > ~/rv/runs/$run/nstat.before 2>/dev/null; cat /proc/net/dev > ~/rv/runs/$run/netdev.before" & done; wait
for p in $PROVS; do "$H/deploy/prov_start.sh" "$p" "$run" $PROV_FLAGS > /dev/null & done; wait
k=$(wc -w <<<"$LGS")
lgrate=$(python3 -c "print($rate/$k)")
start=$(python3 -c "import time; print(int(time.time()*1000) + 30000)")
for v in $sut_vms; do
  extra=""
  [[ $v == "$REDIS_VM" ]] && extra="--redis-info"
  [[ $v == "$PROBE_UNIT" ]] && extra="--redis-probe $(vm_ip $REDIS_VM):6379"
  (rscp_to "$v" /home/rv/rv/pbf_sampler.py "$EVID/scripts/pbf_sampler.py" && \
   rssh "$v" "mkdir -p /dev/shm/pbf/$run/sut && cd /dev/shm/pbf/$run/sut && (curl -s -m 5 localhost:8400/_rv/contract > contract.json || true); \
     nohup python3 ~/rv/pbf_sampler.py --out /dev/shm/pbf/$run/sut --metrics-dir /dev/shm/rv-metrics \
     --start-at $start --ramp $RAMP --warmup $WARMUP --duration $DURATION --tail 30 $extra > sampler.log 2>&1 < /dev/null & echo \$! > sampler.pid") &
done
wait
i=0
for n in $LGS; do
  rssh "$n" "set -e; d=~/rv/runs/$run/lg; rm -rf \$d; mkdir -p \$d; cd \$d; cp ~/rv/hostinfo.txt .; \
    nohup ~/rv/bin/olg -targets $TARGETS -rate $lgrate -lg $i -lg-count $k -start-at $start -out \$d -run-id $run $olgflags \
    > olg.log 2>&1 < /dev/null & echo \$! > olg.pid" &
  i=$((i+1))
done
wait
log "$run: olg started on $LGS (rate $lgrate each, start-at $start, mode $MODE, targets $TARGETS)"
sleep $((RAMP + WARMUP + DURATION + 25))
exec bash "$(dirname "$0")/pbf_finish.sh" "$run"
