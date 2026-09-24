#!/bin/bash
# Claim 3 step: c3_step.sh LABEL CONFIG RATE     CONFIG = A_prod | B_keepalive | direct
# olg (rv-micro-lg-2) -> nginx:1.30-alpine edge (rv-micro-edge-1, port 80, gateway vhost, Host aimeshgateway.zeroshield.ai,
# X-Forwarded-Proto https) -> gunicorn+UvicornWorker x8 minimal ASGI app (rv-micro-up-1:8300). direct = olg -> upstream.
# Open-loop constant rate, 4 KB chat JSON; ramp 5 s + warm-up 30 s excluded; DUR (default 300 s) measured.
set -u
source /tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/micro-claims/bin/lib.sh
LABEL=$1 CONFIG=$2 RATE=$3
DUR=${DUR:-300} WARM=${WARM:-30} RAMP=${RAMP:-5}
CTRL=$E/claim3/${RUNS_SUBDIR:-runs}/$LABEL; RAW=$RAWROOT/claim3/$LABEL
[ -f $CTRL/done.json ] && { echo "skip $LABEL (done)"; exit 0; }
mkdir -p $CTRL
# isolate steps: wait (max 150 s) until TIME_WAIT left by the previous step has drained on edge and upstream
for i in $(seq 1 30); do
  TW=$($RSSH $EDGE1 "ss -H -tn state time-wait | wc -l" < /dev/null); TWU=$($RSSH $UP1 "ss -H -tn state time-wait | wc -l" < /dev/null)
  [ "$TW" -lt 300 ] && [ "$TWU" -lt 300 ] && break; sleep 5
done
echo "time-wait before step: edge=$TW up=$TWU" > $CTRL/tw_before.txt
if [ $CONFIG = direct ]; then TARGET=http://$UP1:8300; $RSSH $EDGE1 "./edge.sh stop" < /dev/null > $CTRL/edge_start.txt
else TARGET=http://aimeshgateway.zeroshield.ai; $RSSH $EDGE1 "./edge.sh start $CONFIG" < /dev/null > $CTRL/edge_start.txt; fi
$RSSH $UP1 "./up.sh app" < /dev/null > $CTRL/up_start.txt
START=$(( $(now_ms) + 5000 )); W0=$(python3 -c "print($START/1000 + $RAMP + $WARM)"); W1=$(python3 -c "print($W0 + $DUR)")
sut_cpu_start $EDGE1 "nginx: worker" $LABEL $((5+RAMP+WARM+DUR+15))
sut_cpu_start $UP1 "gunicorn" $LABEL $((5+RAMP+WARM+DUR+15))
olg_run $LG2 "-targets $TARGET -corpus ~/corpus_4k.jsonl -sse-frac 0 -rate $RATE -duration $DUR -warmup $WARM -ramp $RAMP -sample-mod 1000000 -H X-Forwarded-Proto:https" $LABEL $START $DUR > $CTRL/olg_done_line.txt &
OLGPID=$!
snap() { # tag
  $RSSH $EDGE1 "~/venv/bin/python3 ~/netsnap.py 2>/dev/null || python3 ~/netsnap.py; curl -s http://127.0.0.1:8081/stub_status | tr '\n' ' '; echo" < /dev/null > $CTRL/edge_net_$1.txt &
  $RSSH $UP1 "~/venv/bin/python3 ~/netsnap.py" < /dev/null > $CTRL/up_net_$1.txt &
  wait
}
python3 -c "import time; time.sleep(max(0, $W0 - time.time()))"; snap start
python3 -c "import time; time.sleep(max(0, $W0 + $DUR/2 - time.time()))"; snap mid
python3 -c "import time; time.sleep(max(0, $W1 - time.time()))"; snap end
wait $OLGPID
olg_fetch $LG2 $LABEL $CTRL $RAW
sleep 5
$RSCP rv@$EDGE1:cpu_$LABEL.jsonl $CTRL/edge_cpu.jsonl 2>/dev/null; $RSCP rv@$UP1:cpu_$LABEL.jsonl $CTRL/up_cpu.jsonl
$RSSH $EDGE1 "sudo docker logs edge 2>&1 | grep -v '\" 200 ' | tail -5" < /dev/null > $CTRL/edge_nonok_log_tail.txt 2>&1
$RSSH $UP1 "tail -5 ~/gunicorn.log" < /dev/null > $CTRL/up_log_tail.txt
[ -s $CTRL/edge_cpu.jsonl ] && python3 $E/bin/sutcpu.py $CTRL/edge_cpu.jsonl $(python3 -c "print($W0+2, $W1-2)") $RATE > $CTRL/edge_cpu_summary.json
python3 $E/bin/sutcpu.py $CTRL/up_cpu.jsonl $(python3 -c "print($W0+2, $W1-2)") $RATE > $CTRL/up_cpu_summary.json
python3 - "$CTRL" "$LABEL" "$CONFIG" "$RATE" "$DUR" <<'PY'
import json, re, sys, os
d, label, config, rate, dur = sys.argv[1:]; rate = float(rate); dur = float(dur)
s = json.load(open(f"{d}/summary.json"))
def load(p):
    try: return json.loads(open(p).readline())
    except Exception: return None
row = {"label": label, "config": config, "rate": rate, "instrument": "olg", "olg": s}
reqs = rate * dur
for side in ("edge", "up"):
    a, b = load(f"{d}/{side}_net_start.txt"), load(f"{d}/{side}_net_end.txt"); m = load(f"{d}/{side}_net_mid.txt")
    if a and b:
        dt = {k: b["tcp"][k] - a["tcp"][k] for k in ("ActiveOpens", "PassiveOpens", "AttemptFails", "EstabResets", "OutRsts")}
        row[f"{side}_tcp_delta"] = dt
        row[f"{side}_active_opens_per_req"] = round(dt["ActiveOpens"] / reqs, 4)
        row[f"{side}_passive_opens_per_req"] = round(dt["PassiveOpens"] / reqs, 4)
        row[f"{side}_states_mid"] = m["states"] if m else None
    if os.path.exists(f"{d}/{side}_cpu_summary.json"):
        row[f"{side}_cpu"] = json.load(open(f"{d}/{side}_cpu_summary.json"))
try:
    st = [open(f"{d}/edge_net_{t}.txt").read().splitlines()[1] for t in ("start", "end")]
    nums = [list(map(int, re.search(r"requests\s+(\d+)\s+(\d+)\s+(\d+)", x).groups())) for x in st]
    row["edge_stub_delta"] = {"accepts": nums[1][0] - nums[0][0], "handled": nums[1][1] - nums[0][1], "requests": nums[1][2] - nums[0][2]}
except Exception as e:
    row["edge_stub_delta"] = None
L = s["latency_us"]; ok = s["ok"]; n = s["n_measured"]
row["pass_p99_lt_5ms"] = bool(L and L.get("p99", 1e12) < 5000 and s["drops_gt5ms"] == 0 and (n - ok) <= 0.001 * n)
json.dump(row, open(f"{d}/done.json", "w"))
ec, uc = row.get("edge_cpu", {}), row.get("up_cpu", {})
print(f"{label}: ok={ok}/{n} err={s['errors']} drops={s['drops_gt5ms']} p50={L.get('p50')} p99={L.get('p99')} p99.9={L.get('p99.9')} us "
      f"| edge ActiveOpens/req={row.get('edge_active_opens_per_req')} up PassiveOpens/req={row.get('up_passive_opens_per_req')} "
      f"| TW edge={((row.get('edge_states_mid') or {}).get('time-wait'))} up={((row.get('up_states_mid') or {}).get('time-wait'))} "
      f"| cores edge={ec.get('vm_sched_cores_busy')} up={uc.get('vm_sched_cores_busy')} | PASS={row['pass_p99_lt_5ms']}")
PY
