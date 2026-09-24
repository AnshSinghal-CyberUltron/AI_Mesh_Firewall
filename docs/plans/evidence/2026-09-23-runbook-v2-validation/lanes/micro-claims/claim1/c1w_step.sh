#!/bin/bash
# One claim-1 step with wrk2 (fallback instrument: olg READY not set within 90 min).
# c1w_step.sh SUT LG LABEL VARIANT WORKERS CPUS RATE
# Constant-throughput open-loop schedule (wrk2 -R), CO-corrected HdrHistogram, 4 KB chat POST body.
# 20 s warm-up run (discarded) + 312 s measured run (wrk2 drops its first ~10 s calibration -> ~300 s recorded).
set -u
source /tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/micro-claims/bin/lib.sh
SUT=$1 LG=$2 LABEL=$3 VARIANT=$4 WORKERS=$5 CPUS=$6 RATE=$7
DUR=${DUR:-312}; WARM=${WARM:-20}
if [ "$RATE" -lt 2000 ]; then T=2; C=16; elif [ "$RATE" -lt 20000 ]; then T=4; C=64; else T=8; C=128; fi
CTRL=$E/claim1/runs/$LABEL
[ -f $CTRL/done.json ] && { echo "skip $LABEL (done)"; exit 0; }
mkdir -p $CTRL
URL=http://$SUT:8300/v1/chat/completions
$RSSH $SUT "./sut.sh start $VARIANT $WORKERS $CPUS" < /dev/null > $CTRL/sut_start.txt
$RSSH $LG "ulimit -n 1048576; ~/wrk2/wrk -t$T -c$C -d${WARM}s -R$RATE -s ~/post4k.lua $URL" < /dev/null > $CTRL/wrk2_warmup.txt 2>&1
START=$(now_ms)
sut_cpu_start $SUT "uvicorn mw_app" $LABEL $((DUR+10))
$RSSH $LG "ulimit -n 1048576; ~/wrk2/wrk -t$T -c$C -d${DUR}s -R$RATE --latency --u_latency -s ~/post4k.lua $URL" < /dev/null > $CTRL/wrk2_measure.txt 2>&1
sleep 8
$RSCP rv@$SUT:cpu_$LABEL.jsonl $CTRL/sut_cpu.jsonl
$RSSH $SUT "./sut.sh stop; tail -5 ~/uvicorn_${VARIANT}_${WORKERS}.log" < /dev/null > $CTRL/sut_log_tail.txt
python3 $E/bin/wrk2_parse.py $CTRL/wrk2_measure.txt > $CTRL/summary.json
T0=$(python3 -c "print($START/1000 + 12)"); T1=$(python3 -c "print($START/1000 + $DUR - 2)")
python3 $E/bin/sutcpu.py $CTRL/sut_cpu.jsonl $T0 $T1 $RATE > $CTRL/sut_cpu_summary.json
python3 - "$CTRL" "$LABEL" "$VARIANT" "$WORKERS" "$CPUS" "$RATE" "$START" "$T" "$C" <<'PY'
import json, sys
d, label, variant, workers, cpus, rate, start, t, c = sys.argv[1:]
s = json.load(open(f"{d}/summary.json")); cp = json.load(open(f"{d}/sut_cpu_summary.json"))
row = {"label": label, "instrument": "wrk2", "variant": variant, "workers": int(workers), "cpus": cpus, "rate": float(rate),
       "wrk2_threads": int(t), "wrk2_conns": int(c), "start_ms": int(start), "summary": s, "sut_cpu": cp}
json.dump(row, open(f"{d}/done.json", "w"))
L = s["latency_us"]
print(f"{label}: rps={s.get('achieved_rps')} non2xx={s.get('non_2xx')} sockerr={s.get('socket_errors')} "
      f"p50={L.get('p50')} p90={L.get('p90')} p99={L.get('p99')} p99.9={L.get('p99.9')} max={L.get('p100')} us | "
      f"cpu_us/req={cp['cpu_us_per_req']} vm_cores={cp.get('vm_sched_cores_busy')}")
PY
