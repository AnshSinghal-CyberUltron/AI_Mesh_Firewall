#!/bin/bash
# One claim-1 measurement step: c1_step.sh SUT LG LABEL VARIANT WORKERS CPUS RATE
# open-loop (olg) constant arrivals, JSON (4 KB chat body), ramp 10 s + warm-up 30 s excluded, 300 s measured.
set -u
source /tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/micro-claims/bin/lib.sh
SUT=$1 LG=$2 LABEL=$3 VARIANT=$4 WORKERS=$5 CPUS=$6 RATE=$7
DUR=${DUR:-300} WARM=${WARM:-30} RAMP=${RAMP:-10}
CTRL=$E/claim1/${RUNS_SUBDIR:-runs}/$LABEL; RAW=$RAWROOT/claim1/$LABEL
[ -f $CTRL/done.json ] && { echo "skip $LABEL (done)"; exit 0; }
mkdir -p $CTRL
$RSSH $SUT "./sut.sh start $VARIANT $WORKERS $CPUS" < /dev/null > $CTRL/sut_start.txt
START=$(( $(now_ms) + 5000 ))
sut_cpu_start $SUT "uvicorn mw_app" $LABEL $((5+RAMP+WARM+DUR+15))
OUT=$(olg_run $LG "-targets http://$SUT:8300 -corpus ~/corpus_4k.jsonl -sse-frac 0 -rate $RATE -duration $DUR -warmup $WARM -ramp $RAMP -sample-mod 1000000" $LABEL $START $DUR)
echo "$OUT" > $CTRL/olg_done_line.txt
olg_fetch $LG $LABEL $CTRL $RAW
sleep 10
$RSCP rv@$SUT:cpu_$LABEL.jsonl $CTRL/sut_cpu.jsonl
$RSSH $SUT "./sut.sh stop; tail -5 ~/uvicorn_${VARIANT}_${WORKERS}.log" < /dev/null > $CTRL/sut_log_tail.txt
T0=$(python3 -c "print($START/1000 + $RAMP + $WARM + 2)"); T1=$(python3 -c "print($START/1000 + $RAMP + $WARM + $DUR - 2)")
python3 $E/bin/sutcpu.py $CTRL/sut_cpu.jsonl $T0 $T1 $RATE > $CTRL/sut_cpu_summary.json
python3 - "$CTRL" "$LABEL" "$VARIANT" "$WORKERS" "$CPUS" "$RATE" "$START" <<'PY'
import json, sys
d, label, variant, workers, cpus, rate, start = sys.argv[1:]
s = json.load(open(f"{d}/summary.json")); c = json.load(open(f"{d}/sut_cpu_summary.json"))
m = json.load(open(f"{d}/manifest.json"))
row = {"label": label, "variant": variant, "workers": int(workers), "cpus": cpus, "rate": float(rate),
       "start_at_ms": int(start), "summary": s, "sut_cpu": c,
       "instrument": "olg", "olg_manifest_keys": sorted(m.keys())[:40]}
json.dump(row, open(f"{d}/done.json", "w"))
L = s["latency_us"]
print(f"{label}: ok={s['ok']}/{s['n_measured']} err={s['errors']} drops={s['drops_gt5ms']} opens={s['conn_opens']} "
      f"p50={L.get('p50')} p99={L.get('p99')} p99.9={L.get('p99.9')} max={L.get('max')} us | cpu_us/req={c['cpu_us_per_req']} vm_cores={c.get('vm_sched_cores_busy')}")
PY
