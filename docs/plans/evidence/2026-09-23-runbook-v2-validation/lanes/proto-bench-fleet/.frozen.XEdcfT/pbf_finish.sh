#!/usr/bin/env bash
# pbf_finish.sh RUN   -- second half of pbf_step.sh (wait for olg, stop providers + samplers, nstat after,
# collect, analyze); reads the run's step.json. Used by pbf_step.sh and to finish an interrupted step.
set -euo pipefail
source "$(dirname "$0")/env.sh"
export ZONE=asia-northeast1-a
source "$H/deploy/lib.sh"
run=$1
RUNS=${RUNS:-$RAW/runs}
sj=$RUNS/$run/step.json
get() { python3 -c "import json,sys; print(json.load(open('$sj')).get('$1',''))"; }
LGS=$(get lgs); PROVS=$(get provs); sut_vms=$(get sut_vms); MODE=$(get mode); CORPUS=$(get corpus)
PROFILE_STAGES=${PROFILE_STAGES:-canon,det,sem,resolve,dispatch,out,audit}
for n in $LGS; do
  while rssh "$n" "kill -0 \$(cat ~/rv/runs/$run/lg/olg.pid) 2>/dev/null"; do sleep 5; done
  rssh "$n" "tail -1 ~/rv/runs/$run/lg/olg.log" | sed "s/^/[$n] /"
done
for p in $PROVS; do "$H/deploy/prov_stop.sh" "$p" "$run" > /dev/null & done; wait
for v in $sut_vms; do
  rssh "$v" "p=\$(cat /dev/shm/pbf/$run/sut/sampler.pid); for i in \$(seq 60); do kill -0 \$p 2>/dev/null || break; sleep 1; done; \
    kill -TERM \$p 2>/dev/null; sleep 2; cd /dev/shm/pbf/$run/sut && (tail -300 ~/rv/logs/rvproto-*.log > rvproto-log-tail.txt 2>/dev/null || true); \
    mkdir -p ~/rv/runs/$run; rm -rf ~/rv/runs/$run/sut; mv /dev/shm/pbf/$run/sut ~/rv/runs/$run/sut; rmdir /dev/shm/pbf/$run 2>/dev/null; true" &
done
wait
for v in $LGS $PROVS $sut_vms; do rssh "$v" "nstat -az > ~/rv/runs/$run/nstat.after 2>/dev/null; cat /proc/net/dev > ~/rv/runs/$run/netdev.after" & done; wait
"$H/deploy/collect.sh" "$run" "$RUNS/$run" $LGS $PROVS $sut_vms 2>/dev/null
$PY "$EVID/scripts/pbf_analyze.py" --run "$RUNS/$run" --mode "$MODE" --profile-stages "$PROFILE_STAGES" \
  --corpus "$H/corpora/$CORPUS" > "$RUNS/$run/pbf.stdout" 2>&1 || true
mkdir -p "$EVID/runs/$run"
cp "$RUNS/$run/step.json" "$RUNS/$run"/pbf_summary.* "$EVID/runs/$run/" 2>/dev/null || true
head -${HEADN:-16} "$RUNS/$run/pbf.stdout"
