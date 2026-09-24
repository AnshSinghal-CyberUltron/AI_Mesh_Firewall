#!/usr/bin/env bash
# pbu_overload.sh RUN BASE_RATE BURST_RATE CORPUS_FILE
#   GW19 overload step: lg-1 offers BASE_RATE for the whole timeline (ramp, warm-up, PRE + BURST + POST
#   measured seconds); lg-2 adds BURST_RATE on top for BURST seconds starting PRE seconds into the
#   measurement (offered total during the burst = BASE + BURST). A header probe on lg-2 records
#   status / Retry-After / Retry-After-Ms of small JSON requests every 0.25 s through the burst.
#   The unit sampler covers the whole timeline; dmesg (OOM) and rvproto PIDs are captured before/after.
#   env: PRE=180 BURST=180 POST=180 RAMP=30 WARMUP=60 SSE_FRAC=0.7 AUTH=auth-a PROV_FLAGS OLG_EXTRA
set -euo pipefail
export LANE=${LANE:-pbu}
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
export EVID=${EVID:-$SP/evidence/proto-bench-unit}
source "$SP/harness/deploy/lib.sh"
run=$1 base=$2 burst=$3 corpus=$4
RUNS=${RUNS:-/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs}
LG1=rv-pbu-lg-1 LG2=rv-pbu-lg-2 PROV=rv-pbu-prov-1 UNIT=rv-proto-unit-1
PRE=${PRE:-180} BURST=${BURST:-180} POST=${POST:-180} RAMP=${RAMP:-30} WARMUP=${WARMUP:-60}
SSE_FRAC=${SSE_FRAC:-0.7} AUTH=${AUTH:-auth-a}
PROV_FLAGS=${PROV_FLAGS:-"-ttft 150ms -itl 20ms"}
TOOLS=$EVID/tools
PY=/home/contact_cyberultron_com/AI_Mesh_Firewall/gateway/.venv/bin/python
TARGET=http://10.160.0.46:8400
[[ -d "$RUNS/$run" ]] && { log "refusing: $RUNS/$run exists"; exit 1; }
mkdir -p "$RUNS/$run"
cfile=/home/rv/rv/corpora/$(basename "$corpus")
dur=$((PRE + BURST + POST))
tag=$((RANDOM % 256))
cat > "$RUNS/$run/step.json" <<EOF
{"run": "$run", "kind": "overload", "base_rate": $base, "burst_rate": $burst, "pre_s": $PRE, "burst_s": $BURST,
 "post_s": $POST, "ramp_s": $RAMP, "warmup_s": $WARMUP, "corpus": "$(basename "$corpus")", "sse_frac": $SSE_FRAC,
 "auth": "$AUTH", "prov_flags": "$PROV_FLAGS", "run_tag": $tag, "created_utc": "$(date -u +%FT%TZ)"}
EOF
for v in $LG1 $LG2 $PROV $UNIT; do rssh "$v" "mkdir -p ~/rv/runs/$run && nstat -az > ~/rv/runs/$run/nstat.before 2>/dev/null; cat /proc/net/dev > ~/rv/runs/$run/netdev.before" & done; wait
rssh "$UNIT" "mkdir -p /dev/shm/pbu/$run/unit && sudo dmesg -T > /dev/shm/pbu/$run/unit/dmesg.before; pgrep -af rvproto.serve > /dev/shm/pbu/$run/unit/pids.before"
"$SP/harness/deploy/prov_start.sh" "$PROV" "$run" $PROV_FLAGS
start=$(python3 -c "import time; print(int(time.time()*1000) + 25000)")
bstart=$((start + (RAMP + WARMUP + PRE) * 1000))
rscp_to "$UNIT" /home/rv/rv/unit_sampler.py "$TOOLS/unit_sampler.py"
rssh "$UNIT" "cd /dev/shm/pbu/$run/unit && curl -s -m 5 localhost:8400/_rv/contract > contract.json; \
  nohup python3 ~/rv/unit_sampler.py --out /dev/shm/pbu/$run/unit --metrics-dir ${UNIT_METRICS:-/dev/shm/rv-metrics} \
  --start-at $start --ramp $RAMP --warmup $WARMUP --duration $dur --tail 60 > sampler.log 2>&1 < /dev/null & echo \$! > sampler.pid"
common="-corpus $cfile -sse-frac $SSE_FRAC -auth-file /home/rv/rv/$AUTH -run-tag $tag -run-id $run ${OLG_EXTRA:-}"
rssh "$LG1" "set -e; d=~/rv/runs/$run/lg; rm -rf \$d; mkdir -p \$d; cd \$d; cp ~/rv/hostinfo.txt .; \
  nohup ~/rv/bin/olg -targets $TARGET -rate $base -lg 0 -lg-count 2 -start-at $start -ramp $RAMP -warmup $WARMUP \
  -duration $dur -out \$d $common > olg.log 2>&1 < /dev/null & echo \$! > olg.pid"
rssh "$LG2" "set -e; d=~/rv/runs/$run/lg; rm -rf \$d; mkdir -p \$d; cd \$d; cp ~/rv/hostinfo.txt .; \
  nohup ~/rv/bin/olg -targets $TARGET -rate $burst -lg 1 -lg-count 2 -start-at $bstart -ramp 0 -warmup 0 \
  -duration $BURST -out \$d $common > olg.log 2>&1 < /dev/null & echo \$! > olg.pid"
# header probe: small JSON requests every 0.25 s from 10 s before to 10 s after the burst
rscp_to "$LG2" /home/rv/rv/probe.sh "$TOOLS/probe.sh"
rssh "$LG2" "chmod +x ~/rv/probe.sh; nohup ~/rv/probe.sh $TARGET /home/rv/rv/$AUTH ~/rv/runs/$run/probe.jsonl \
  $((bstart - 10000)) $((bstart + BURST * 1000 + 10000)) $run > ~/rv/runs/$run/probe.log 2>&1 < /dev/null &"
log "$run: base $base/s on $LG1 from $start; burst $burst/s on $LG2 from $bstart for ${BURST}s"
for n in $LG1 $LG2; do
  while rssh "$n" "kill -0 \$(cat ~/rv/runs/$run/lg/olg.pid) 2>/dev/null"; do sleep 10; done
  rssh "$n" "tail -1 ~/rv/runs/$run/lg/olg.log" | sed "s/^/[$n] /"
done
"$SP/harness/deploy/prov_stop.sh" "$PROV" "$run"
rssh "$UNIT" "p=\$(cat /dev/shm/pbu/$run/unit/sampler.pid); for i in \$(seq 120); do kill -0 \$p 2>/dev/null || break; sleep 1; done; \
  kill -TERM \$p 2>/dev/null; sleep 2; cd /dev/shm/pbu/$run/unit && curl -s -m 10 localhost:8400/metrics/all > metrics_all.post.json; \
  sudo dmesg -T > dmesg.after; pgrep -af rvproto.serve > pids.after; free -m > free.after; \
  tail -300 ~/rv/logs/rvproto-*.log > rvproto-log-tail.txt 2>/dev/null; mkdir -p ~/rv/runs/$run; rm -rf ~/rv/runs/$run/unit; \
  mv /dev/shm/pbu/$run/unit ~/rv/runs/$run/unit; rmdir /dev/shm/pbu/$run 2>/dev/null; true"
for v in $LG1 $LG2 $PROV $UNIT; do rssh "$v" "nstat -az > ~/rv/runs/$run/nstat.after 2>/dev/null; cat /proc/net/dev > ~/rv/runs/$run/netdev.after" & done; wait
"$SP/harness/deploy/collect.sh" "$run" "$RUNS/$run" $LG1 $LG2 $PROV $UNIT
$PY "$TOOLS/overload_analyze.py" --run "$RUNS/$run" > "$RUNS/$run/overload.stdout" 2>&1 || true
mkdir -p "$EVID/runs/$run"
cp "$RUNS/$run/step.json" "$RUNS/$run"/overload_summary.* "$EVID/runs/$run/" 2>/dev/null || true
touch "$RUNS/$run/ANALYZED"
tail -60 "$RUNS/$run/overload.stdout"
