#!/usr/bin/env bash
# pbu_step.sh RUN TOTAL_RATE CORPUS_FILE
#   One open-loop measurement step for the proto-bench-unit lane (adapted from harness deploy/step.sh:
#   same fresh-synthprov / synchronized-olg / nstat / collect sequence, plus a SUT-side sampler on the
#   unit started for the same wall-clock schedule, and both analyzers).
#   env:
#     MODE=sut|direct (default sut)     TARGETS (sut default http://10.160.0.46:8400)
#     AUTH=auth-a|auth-b|none           PROV_FLAGS (default "-ttft 150ms -itl 20ms")
#     RAMP=30 WARMUP=60 DURATION=300    SSE_FRAC=0.7   OLG_EXTRA="" (e.g. -sample-mod 1)
#     LGS="rv-pbu-lg-1 rv-pbu-lg-2"     PROV=rv-pbu-prov-1   UNIT=rv-proto-unit-1
#     PROFILE_STAGES (default canon,det,sem,resolve,dispatch,out,audit)  PBU_FLAGS (extra pbu_analyze flags)
#     LABEL (free text recorded in step.json)
set -euo pipefail
export LANE=${LANE:-pbu}
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
export EVID=${EVID:-$SP/evidence/proto-bench-unit}
source "$SP/harness/deploy/lib.sh"
run=$1 rate=$2 corpus=$3
MODE=${MODE:-sut}
RUNS=${RUNS:-/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs}
LGS=${LGS:-"rv-pbu-lg-3 rv-pbu-lg-4"}
PROV=${PROV:-rv-pbu-prov-2}
UNIT=${UNIT:-rv-proto-unit-1}
RAMP=${RAMP:-30} WARMUP=${WARMUP:-60} DURATION=${DURATION:-300} SSE_FRAC=${SSE_FRAC:-0.7}
PROV_FLAGS=${PROV_FLAGS:-"-ttft 150ms -itl 20ms"}
AUTH=${AUTH:-auth-a}
PROFILE_STAGES=${PROFILE_STAGES:-canon,det,sem,resolve,dispatch,out,audit}
TOOLS=$EVID/tools
PY=/home/contact_cyberultron_com/AI_Mesh_Firewall/gateway/.venv/bin/python
[[ -d "$RUNS/$run" ]] && { log "refusing: $RUNS/$run exists"; exit 1; }
mkdir -p "$RUNS/$run"
if [[ $MODE == direct ]]; then
  ip=$(vm_ip "$PROV"); TARGETS=""
  for port in 8080 8081 8082 8083; do TARGETS+="http://$ip:$port,"; done
  TARGETS=${TARGETS%,}
  extra=""
else
  TARGETS=${TARGETS:-http://10.160.0.46:8400}
  extra=$UNIT
fi
authflag=""
[[ $AUTH != none ]] && authflag="-auth-file /home/rv/rv/$AUTH"
olgflags="-corpus /home/rv/rv/corpora/$(basename "$corpus") -ramp $RAMP -warmup $WARMUP -duration $DURATION -sse-frac $SSE_FRAC $authflag ${OLG_EXTRA:-}"
cat > "$RUNS/$run/step.json" <<EOF
{"run": "$run", "rate": $rate, "mode": "$MODE", "corpus": "$(basename "$corpus")", "targets": "$TARGETS",
 "auth": "$AUTH", "prov_flags": "$PROV_FLAGS", "olg_flags": "$olgflags", "lgs": "$LGS", "prov": "$PROV",
 "unit": "$extra", "label": "${LABEL:-}", "created_utc": "$(date -u +%FT%TZ)"}
EOF
for v in $LGS $PROV $extra; do rssh "$v" "mkdir -p ~/rv/runs/$run && nstat -az > ~/rv/runs/$run/nstat.before 2>/dev/null; cat /proc/net/dev > ~/rv/runs/$run/netdev.before" & done; wait
"$SP/harness/deploy/prov_start.sh" "$PROV" "$run" $PROV_FLAGS
k=$(wc -w <<<"$LGS")
lgrate=$(python3 -c "print($rate/$k)")
start=$(python3 -c "import time; print(int(time.time()*1000) + 25000)")
if [[ -n $extra ]]; then
  rscp_to "$UNIT" /home/rv/rv/unit_sampler.py "$TOOLS/unit_sampler.py"
  # sampler output on tmpfs during the step (no measurement I/O on the SUT's disk); moved after the step
  rssh "$UNIT" "mkdir -p /dev/shm/pbu/$run/unit && cd /dev/shm/pbu/$run/unit && curl -s -m 5 localhost:8400/_rv/contract > contract.json; \
    nohup python3 ~/rv/unit_sampler.py --out /dev/shm/pbu/$run/unit --metrics-dir ${UNIT_METRICS:-/dev/shm/rv-metrics} \
    --start-at $start --ramp $RAMP --warmup $WARMUP --duration $DURATION --tail 40 ${SAMPLER_FLAGS:-} > sampler.log 2>&1 < /dev/null & echo \$! > sampler.pid"
fi
i=0
for n in $LGS; do
  rssh "$n" "set -e; d=~/rv/runs/$run/lg; rm -rf \$d; mkdir -p \$d; cd \$d; cp ~/rv/hostinfo.txt .; \
    nohup ~/rv/bin/olg -targets $TARGETS -rate $lgrate -lg $i -lg-count $k -start-at $start -out \$d -run-id $run $olgflags \
    > olg.log 2>&1 < /dev/null & echo \$! > olg.pid" &
  i=$((i+1))
done
wait
log "$run: olg started on $LGS (rate $lgrate each, start-at $start, mode $MODE, targets $TARGETS)"
for n in $LGS; do
  while rssh "$n" "kill -0 \$(cat ~/rv/runs/$run/lg/olg.pid) 2>/dev/null"; do sleep 10; done
  rssh "$n" "tail -1 ~/rv/runs/$run/lg/olg.log" | sed "s/^/[$n] /"
done
"$SP/harness/deploy/prov_stop.sh" "$PROV" "$run"
if [[ -n $extra ]]; then
  rssh "$UNIT" "p=\$(cat /dev/shm/pbu/$run/unit/sampler.pid); for i in \$(seq 90); do kill -0 \$p 2>/dev/null || break; sleep 1; done; \
    kill -TERM \$p 2>/dev/null; sleep 2; cd /dev/shm/pbu/$run/unit && curl -s -m 10 localhost:8400/metrics/all > metrics_all.post.json; \
    tail -200 ~/rv/logs/rvproto-*.log > rvproto-log-tail.txt 2>/dev/null; mkdir -p ~/rv/runs/$run; rm -rf ~/rv/runs/$run/unit; \
    mv /dev/shm/pbu/$run/unit ~/rv/runs/$run/unit; rmdir /dev/shm/pbu/$run 2>/dev/null; true"
fi
for v in $LGS $PROV $extra; do rssh "$v" "nstat -az > ~/rv/runs/$run/nstat.after 2>/dev/null; cat /proc/net/dev > ~/rv/runs/$run/netdev.after" & done; wait
"$SP/harness/deploy/collect.sh" "$run" "$RUNS/$run" $LGS $PROV $extra
cl=(); for l in $LGS; do cl+=("$RUNS/$run/$l/lg"); done
hmode=$MODE; hflags=""
[[ $MODE == sut ]] && hflags="--profile-stages $PROFILE_STAGES"
cpath="$SP/harness/corpora/$(basename "$corpus")"
[[ -f "$EVID/corpora/$(basename "$corpus")" ]] && cpath="$EVID/corpora/$(basename "$corpus")"
analyze_all() {
  $PY "$TOOLS/pinned/analyze_c9f89fe8.py" --client "${cl[@]}" --provider "$RUNS/$run/$PROV/prov" --mode "$hmode" $hflags \
    --out "$RUNS/$run/analysis" > "$RUNS/$run/analyze.stdout" 2>&1 || true
  $PY "$TOOLS/pbu_analyze.py" --run "$RUNS/$run" --mode "$MODE" --profile-stages "$PROFILE_STAGES" \
    --corpus "$cpath" ${PBU_FLAGS:-} > "$RUNS/$run/pbu.stdout" 2>&1 || true
  if [[ "${OLG_EXTRA:-}" == *"-sample-mod 1"* ]]; then  # corrected metrics (rules a-d) need 100% chunk data
    $PY "$TOOLS/pbu_c4.py" --run "$RUNS/$run" --mode "$MODE" --profile-stages "$PROFILE_STAGES" --corpus "$cpath" \
      > "$RUNS/$run/pbu_c4.stdout" 2>&1 || true
  fi
  mkdir -p "$EVID/runs/$run"
  cp "$RUNS/$run/step.json" "$EVID/runs/$run/"
  cp -r "$RUNS/$run/analysis" "$EVID/runs/$run/harness-analysis" 2>/dev/null || true
  cp "$RUNS/$run"/pbu_summary.* "$RUNS/$run"/pbu_c4.* "$EVID/runs/$run/" 2>/dev/null || true
  touch "$RUNS/$run/ANALYZED"
}
if [[ ${ASYNC:-0} == 1 ]]; then
  analyze_all > /dev/null 2>&1 &
  disown
  log "$run: analysis running in background"
else
  analyze_all
  head -12 "$RUNS/$run/pbu.stdout"
fi
