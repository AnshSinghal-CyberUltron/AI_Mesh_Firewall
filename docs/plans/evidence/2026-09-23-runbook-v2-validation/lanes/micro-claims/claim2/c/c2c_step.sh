#!/bin/bash
# Claim 2(c) step: c2c_step.sh LABEL EMITS MODE LEVEL NETEM_MS RATE REPLAYFILE
# SUT rv-micro-app-1 (1 uvicorn worker, vCPU 2) -> Redis on rv-micro-redis-1 (netem on its egress);
# olg on rv-micro-lg-4, open-loop constant rate, JSON; ramp 5 s + warm-up 30 s excluded; 300 s measured.
set -u
source /tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/micro-claims/bin/lib.sh
LABEL=$1 EMITS=$2 MODE=$3 LEVEL=$4 NETEM=$5 RATE=$6 REPLAY=$7
DUR=${DUR:-300} WARM=${WARM:-30} RAMP=${RAMP:-5}
CTRL=$E/claim2/c/runs/$LABEL; RAW=$RAWROOT/claim2c/$LABEL
[ -f $CTRL/done.json ] && { echo "skip $LABEL (done)"; exit 0; }
mkdir -p $CTRL
$RSSH $REDIS1 "sudo tc qdisc del dev ens3 root 2>/dev/null; if [ $NETEM != 0 ]; then sudo tc qdisc add dev ens3 root netem delay ${NETEM}ms limit 100000; fi; tc qdisc show dev ens3 | head -1" < /dev/null > $CTRL/netem.txt
$RSSH $APP1 "REPLAY=~/$REPLAY ./app.sh start $EMITS $MODE $LEVEL" < /dev/null > $CTRL/app_start.txt
$RSSH $APP1 "ping -q -c 200 -i 0.01 $REDIS1 | tail -1" < /dev/null > $CTRL/icmp_rtt.txt
START=$(( $(now_ms) + 5000 ))
sut_cpu_start $APP1 "uvicorn loglag_app" $LABEL $((5+RAMP+WARM+DUR+15))
olg_run $LG4 "-targets http://$APP1:8300 -corpus ~/corpus_small.jsonl -sse-frac 0 -rate $RATE -duration $DUR -warmup $WARM -ramp $RAMP -sample-mod 1000000" $LABEL $START $DUR > $CTRL/olg_done_line.txt &
OLGPID=$!
python3 -c "import time; time.sleep(max(0, $START/1000 + $RAMP + $WARM - time.time()))"
$RSSH $REDIS1 "redis-cli INFO commandstats | grep -E '^cmdstat_publish:' || echo cmdstat_publish:calls=0" < /dev/null > $CTRL/redis_publish_before.txt
$RSSH $APP1 "curl -s 'http://127.0.0.1:8300/_lag?reset=1'" < /dev/null > $CTRL/lag_at_reset.json
python3 -c "import time; time.sleep(max(0, $START/1000 + $RAMP + $WARM + $DUR - time.time()))"
$RSSH $APP1 "curl -s 'http://127.0.0.1:8300/_lag?dump=~/lag_$LABEL.json'" < /dev/null > $CTRL/lag_summary.json
$RSSH $REDIS1 "redis-cli INFO commandstats | grep -E '^cmdstat_publish:' || echo cmdstat_publish:calls=0" < /dev/null > $CTRL/redis_publish_after.txt
wait $OLGPID
olg_fetch $LG4 $LABEL $CTRL $RAW
sleep 5
$RSCP rv@$APP1:cpu_$LABEL.jsonl $CTRL/sut_cpu.jsonl; $RSCP rv@$APP1:lag_$LABEL.json $CTRL/lag_samples.json
$RSSH $APP1 "ss -tin dst $REDIS1" < /dev/null > $CTRL/app_redis_sockets.txt   # per-socket retrans counters (app->Redis)
$RSSH $APP1 "./app.sh stop; tail -3 ~/loglag_${EMITS}_${MODE}.log" < /dev/null > $CTRL/app_log_tail.txt
$RSSH $REDIS1 "sudo tc qdisc del dev ens3 root 2>/dev/null; true" < /dev/null
T0=$(python3 -c "print($START/1000 + $RAMP + $WARM + 2)"); T1=$(python3 -c "print($START/1000 + $RAMP + $WARM + $DUR - 2)")
python3 $E/bin/sutcpu.py $CTRL/sut_cpu.jsonl $T0 $T1 $RATE > $CTRL/sut_cpu_summary.json
python3 - "$CTRL" "$LABEL" "$EMITS" "$MODE" "$LEVEL" "$NETEM" "$RATE" "$REPLAY" <<'PY'
import json, re, sys
d, label, emits, mode, level, netem, rate, replay = sys.argv[1:]
s = json.load(open(f"{d}/summary.json")); lag = json.load(open(f"{d}/lag_summary.json")); c = json.load(open(f"{d}/sut_cpu_summary.json"))
calls = lambda f: int(re.search(r"calls=(\d+)", open(f).read()).group(1))
pub = calls(f"{d}/redis_publish_after.txt") - calls(f"{d}/redis_publish_before.txt")
row = {"label": label, "emits_cfg": int(emits), "mode": mode, "gw_level": level, "netem_ms": float(netem), "rate": float(rate),
       "replay": replay, "icmp": open(f"{d}/icmp_rtt.txt").read().strip(), "redis_publish_calls_in_window": pub,
       "publishes_per_req": round(pub / max(1, lag["reqs"]), 3), "olg": s, "lag": lag, "sut_cpu": c, "instrument": "olg"}
json.dump(row, open(f"{d}/done.json", "w"))
L = s["latency_us"]; T = lag["timer_lag_us"]; S = lag["xthread_lag_us"]; B = lag["blocked_us_per_req"]
print(f"{label}: ok={s['ok']}/{s['n_measured']} drops={s['drops_gt5ms']} lat p50={L.get('p50')} p99={L.get('p99')} | pub/req={row['publishes_per_req']} "
      f"blocked/req p50={B['p50']} p99={B['p99']} us | timer-lag p50={T['p50']} p99={T['p99']} max={T['max']} | xthread p50={S['p50']} p99={S['p99']} max={S['max']} us | cpu_us/req={c['cpu_us_per_req']}")
PY
