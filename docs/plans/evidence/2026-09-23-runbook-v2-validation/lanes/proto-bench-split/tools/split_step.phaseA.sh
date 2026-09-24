#!/usr/bin/env bash
# split_step.sh RUN TOTAL_RATE CORPUS_FILE
#   One open-loop measurement step for the split lane (gateway VM + separate GPU guard VM over TCP).
#   Adapted from proto-bench-unit tools/pbu_step.sh (itself harness deploy/step.sh): fresh synthprov,
#   nstat + /proc/net/dev before/after on every VM, synchronized olg start, and the same SUT-side
#   sampler (tools/sampler.py = unit lane unit_sampler.py) on the gateway VM AND on every guard VM,
#   scheduled for the same wall-clock window; collect; then three analyses:
#     analysis/            harness analyze.py at READY (pinned/analyze_3874eaac.py): strata, validity, drops
#     split_metrics.json   the controller's corrected rules (tools/split_metrics.py)
#     split_sut.json       CPU / GPU / guard RTT / owner queue / bytes attribution (tools/split_sut.py)
#   env:
#     MODE=sut|direct (default sut)      TARGETS (sut default http://<gateway ip>:8400)
#     AUTH=auth-a|none                   PROV_FLAGS (default "-ttft 150ms -itl 20ms -sample-mod 1")
#     OLG_EXTRA (default "-sample-mod 1", e.g. add "-arrival poisson")   SAMPLE_MOD (default 1)
#     RAMP=30 WARMUP=60 DURATION=300 SSE_FRAC=0.7
#     LGS=rv-split-lg-1  PROV=rv-split-prov-1  GW=rv-split-gw-1  GUARDS="rv-split-guard-1"
#     PROFILE_STAGES (default canon,det,sem,resolve,dispatch,out,audit)  LABEL  ASYNC=1 (analyze in bg)
set -euo pipefail
export LANE=${LANE:-split}
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
export EVID=${EVID:-$SP/evidence/proto-bench-split}
source "$SP/harness/deploy/lib.sh"
run=$1 rate=$2 corpus=$3
MODE=${MODE:-sut}
RUNS=${RUNS:-/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs}
LGS=${LGS:-rv-split-lg-1}
PROV=${PROV:-rv-split-prov-1}
GW=${GW:-rv-split-gw-1}
GUARDS=${GUARDS:-rv-split-guard-1}
RAMP=${RAMP:-30} WARMUP=${WARMUP:-60} DURATION=${DURATION:-300} SSE_FRAC=${SSE_FRAC:-0.7}
SAMPLE_MOD=${SAMPLE_MOD:-1}
PROV_FLAGS=${PROV_FLAGS:-"-ttft 150ms -itl 20ms -sample-mod $SAMPLE_MOD"}
OLG_EXTRA=${OLG_EXTRA:-"-sample-mod $SAMPLE_MOD"}
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
  sut=""
  AUTH=none
else
  TARGETS=${TARGETS:-http://$(vm_ip "$GW"):8400}
  sut="$GW $GUARDS"
fi
authflag=""
[[ $AUTH != none ]] && authflag="-auth-file /home/rv/rv/$AUTH"
olgflags="-corpus /home/rv/rv/corpora/$(basename "$corpus") -ramp $RAMP -warmup $WARMUP -duration $DURATION -sse-frac $SSE_FRAC $authflag $OLG_EXTRA"
cat > "$RUNS/$run/step.json" <<EOF
{"run": "$run", "rate": $rate, "mode": "$MODE", "corpus": "$(basename "$corpus")", "targets": "$TARGETS",
 "auth": "$AUTH", "prov_flags": "$PROV_FLAGS", "olg_flags": "$olgflags", "lgs": "$LGS", "prov": "$PROV",
 "gw": "$GW", "guards": "$GUARDS", "sut_vms": "$sut", "sample_mod": $SAMPLE_MOD, "label": "${LABEL:-}",
 "created_utc": "$(date -u +%FT%TZ)"}
EOF
for v in $LGS $PROV $sut; do rssh "$v" "mkdir -p ~/rv/runs/$run && nstat -az > ~/rv/runs/$run/nstat.before 2>/dev/null; cat /proc/net/dev > ~/rv/runs/$run/netdev.before" & done; wait
"$SP/harness/deploy/prov_start.sh" "$PROV" "$run" $PROV_FLAGS
k=$(wc -w <<<"$LGS")
lgrate=$(python3 -c "print($rate/$k)")
start=$(python3 -c "import time; print(int(time.time()*1000) + 25000)")
for v in $sut; do
  rscp_to "$v" /home/rv/rv/sampler.py "$TOOLS/sampler.py"
  # sampler output on tmpfs during the step (no measurement I/O on the SUT's disk); moved after the step
  rssh "$v" "mkdir -p /dev/shm/split/$run/sut && cd /dev/shm/split/$run/sut && (curl -s -m 5 localhost:8400/_rv/contract > contract.json || true); \
    nohup python3 ~/rv/sampler.py --out /dev/shm/split/$run/sut --metrics-dir /dev/shm/rv-metrics \
    --start-at $start --ramp $RAMP --warmup $WARMUP --duration $DURATION --tail 40 > sampler.log 2>&1 < /dev/null & echo \$! > sampler.pid"
done
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
for v in $sut; do
  rssh "$v" "p=\$(cat /dev/shm/split/$run/sut/sampler.pid); for i in \$(seq 90); do kill -0 \$p 2>/dev/null || break; sleep 1; done; \
    kill -TERM \$p 2>/dev/null; sleep 2; cd /dev/shm/split/$run/sut && (curl -s -m 10 localhost:8400/metrics/all > metrics_all.post.json || true); \
    tail -200 ~/rv/logs/*.log > log-tail.txt 2>/dev/null; mkdir -p ~/rv/runs/$run; rm -rf ~/rv/runs/$run/sut; \
    mv /dev/shm/split/$run/sut ~/rv/runs/$run/sut; rmdir /dev/shm/split/$run 2>/dev/null; true" &
done
wait
for v in $LGS $PROV $sut; do rssh "$v" "nstat -az > ~/rv/runs/$run/nstat.after 2>/dev/null; cat /proc/net/dev > ~/rv/runs/$run/netdev.after" & done; wait
"$SP/harness/deploy/collect.sh" "$run" "$RUNS/$run" $LGS $PROV $sut
cl=(); for l in $LGS; do cl+=("$RUNS/$run/$l/lg"); done
hflags="--mode $MODE"
[[ $MODE == sut ]] && hflags="$hflags --profile-stages $PROFILE_STAGES --policy enforce"
cpath="$SP/harness/corpora/$(basename "$corpus")"
analyze_all() {  # corrected-rules verdict first (the ladder driver waits for it); the rest in background
  $PY "$TOOLS/split_metrics.py" --run "$RUNS/$run" --mode "$MODE" --corpus "$cpath" --stages "$PROFILE_STAGES" \
    --sample-mod "$SAMPLE_MOD" --out "$RUNS/$run/split_metrics.json" > "$RUNS/$run/split_metrics.stdout" 2>&1 || true
  mkdir -p "$EVID/runs/$run"
  cp "$RUNS/$run/step.json" "$RUNS/$run/split_metrics.json" "$RUNS/$run/split_metrics.stdout" "$EVID/runs/$run/" 2>/dev/null || true
  (
    $PY "$TOOLS/pinned/analyze_3874eaac.py" --client "${cl[@]}" --provider "$RUNS/$run/$PROV/prov" $hflags \
      --corpus "$cpath" --out "$RUNS/$run/analysis" > "$RUNS/$run/analyze.stdout" 2>&1 || true
    [[ $MODE == sut ]] && $PY "$TOOLS/split_sut.py" --run "$RUNS/$run" --gw "$GW" --guards "$GUARDS" \
      --out "$RUNS/$run/split_sut.json" > "$RUNS/$run/split_sut.stdout" 2>&1
    cp -r "$RUNS/$run/analysis" "$EVID/runs/$run/harness-analysis" 2>/dev/null || true
    cp "$RUNS/$run"/split_sut.json "$RUNS/$run"/*.stdout "$EVID/runs/$run/" 2>/dev/null || true
    touch "$RUNS/$run/ANALYZED"
  ) > /dev/null 2>&1 &
  disown
}
analyze_all
head -8 "$RUNS/$run/split_metrics.stdout"
