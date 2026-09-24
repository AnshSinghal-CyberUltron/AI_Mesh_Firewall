#!/usr/bin/env bash
# run_step.sh MODE RUN RATE WARMUP_S DURATION_S [CORPUS] [EXTRA_OLG_FLAGS...]   (env RAMP_S, default 30)
#   Mirrors harness deploy/step.sh (fresh synthprov, nstat before/after, synchronized olg, collect) and adds
#   the v1-specific pieces: SUT cgroup sampler + /health probe, SUT logs, v1 audit-event export, the
#   v1_adapter (disp/stages from v1's own audit events) and sut_resources.py.
#   MODE=sut    : loadgen -> v1 gateway (rv-v1-sut-1:8300) -> synthprov; SUT sampler/probe/logs/audit export
#   MODE=direct : loadgen -> synthprov (network/HTTP floor at the same rate)
# Everything raw lands in $E/runs/RUN; analysis is recomputed from those files.
set -euo pipefail
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
E=$SP/evidence/v1-bench
export LANE=v1 EVID=$E
source $SP/harness/deploy/lib.sh
PY=/home/contact_cyberultron_com/AI_Mesh_Firewall/gateway/.venv/bin/python
mode=$1 run=$2 rate=$3 warm=$4 dur=$5 corpus=${6:-headline-22M.jsonl}; shift 5; [ $# -gt 0 ] && shift
extra="$*"
SUT=${SUT_VM:-rv-v1-sut-2} PROV=${PROV_VM:-rv-v1-prov-2} LG=${LG_VM:-rv-v1-lg-2}
PROV_FLAGS="${PROV_FLAGS:--ttft 150ms -itl 20ms}"
R=$E/runs/$run; mkdir -p "$R"
if [ "$mode" = sut ]; then target=http://$(vm_ip $SUT):8300; auth="-auth-file /home/rv/rv/auth.txt -model synth-1"
else target=http://$(vm_ip $PROV):8080; auth=""; fi
RAMP_S=${RAMP_S:-30}
echo "{\"mode\":\"$mode\",\"run\":\"$run\",\"rate\":$rate,\"ramp_s\":$RAMP_S,\"warmup_s\":$warm,\"duration_s\":$dur,\"corpus\":\"$corpus\",\"prov_flags\":\"$PROV_FLAGS\",\"extra\":\"$extra\",\"target\":\"$target\",\"started_utc\":\"$(date -u +%FT%TZ)\"}" > "$R/step.json"
for v in $LG $PROV $SUT; do rssh "$v" "mkdir -p ~/rv/runs/$run && nstat -az > ~/rv/runs/$run/nstat.before 2>/dev/null" & done; wait
# instrument identity for this run (controller condition: sha256 of olg / synthprov / analyze.py per run)
{ echo "## $(date -u +%FT%TZ) run=$run"; for v in $LG $PROV; do echo "## $v"; rssh "$v" "cd ~/rv/bin && sha256sum olg synthprov rvproxy"; done
  echo "## controller analyze.py (harness git $(git -C $SP/harness rev-parse --short HEAD))"; sha256sum $SP/harness/analyze.py; sha256sum $E/setup/v1_adapter.py; } > "$R/instrument.sha256" 2>&1
bash $SP/harness/deploy/prov_start.sh $PROV "$run" $PROV_FLAGS
T0=$(date -u +%Y-%m-%dT%H:%M:%SZ)
[ "$mode" = sut ] && rssh $SUT "bash ~/sut_step.sh start $run"
START_DELAY_S=${START_DELAY_S:-5} bash $SP/harness/deploy/olg_run.sh "$run" "$rate" "$target" \
  "-corpus /home/rv/rv/corpora/$corpus -ramp $RAMP_S -warmup $warm -duration $dur -label v1bench-$run $auth $extra" $LG
sleep 2
bash $SP/harness/deploy/prov_stop.sh $PROV "$run"
for v in $LG $PROV $SUT; do rssh "$v" "nstat -az > ~/rv/runs/$run/nstat.after 2>/dev/null" & done; wait
if [ "$mode" = sut ]; then
  rssh $SUT "bash ~/sut_step.sh stop $run"
  # wait for v1's telemetry drain (gateway -> redis -> control -> postgres) to settle
  prev=-1
  for i in $(seq 1 36); do
    n=$(rssh $SUT "bash ~/v1_events_export.sh $T0 /home/rv/runs/$run/v1-events.jsonl")
    [ "$n" = "$prev" ] && break; prev=$n; sleep 5
  done
  log "v1 audit events exported: $n"
fi
bash $SP/harness/deploy/collect.sh "$run" "$R" $LG $PROV
if [ "$mode" = sut ]; then
  rssh $SUT "cd ~/runs/$run && find . -name '*.jsonl' -size +0 -exec zstd -q -T0 -3 --rm {} \;" || true
  mkdir -p "$R/sut" && rscp_from $SUT "~/runs/$run/." "$R/sut/" && rscp_from $SUT "~/rv/runs/$run/." "$R/sut/" && rssh $SUT "rm -rf ~/rv/runs/$run"
  $PY $E/setup/v1_adapter.py --olg $R/$LG/lg --events <(zstdcat $R/sut/v1-events.jsonl.zst) --out-dir $R/lg-v1aug > "$R/adapter.txt"
  $PY $SP/harness/analyze.py --client $R/lg-v1aug --provider $R/$PROV/prov --mode sut \
     --profile-stages auth,kill_switch,rate_limit,policy,input_scan,model_routing,model_input,model_output,output_guardrail \
     --corpus $SP/harness/corpora/$corpus --out $R/analysis > "$R/analyze.txt" 2>&1 || true
  $PY $E/setup/sut_resources.py --sut-dir $R/sut --lg-dir $R/$LG/lg --out $R/sut_resources.json > /dev/null
else
  $PY $SP/harness/analyze.py --client $R/$LG/lg --provider $R/$PROV/prov --mode direct --out $R/analysis > "$R/analyze.txt" 2>&1 || true
fi
head -3 "$R/analyze.txt"
