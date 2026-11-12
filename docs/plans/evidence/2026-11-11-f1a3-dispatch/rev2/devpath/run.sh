#!/usr/bin/env bash
# F1a-3 rev 2: CPU per qualified-shaped streamed request through develop's gateway_v2 dev profile
# (auth -> plan -> admission -> input scan -> routed httpx dispatch -> incremental output scan ->
# finalize -> audit), on paced upstream streams. Measures the GATEWAY process's CPU (utime+stime
# from /proc, all threads) per qualified request, minus an idle baseline of the same wall time.
# Usage: GWPY=<venv python with serve extras> BENCHPY=<bench venv python> GW=<wt>/gateway_v2 \
#        ./run.sh <label> <events> <gap_ms> <requests> <concurrency>
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); OUT=$HERE/out; mkdir -p "$OUT"
LABEL=$1 EVENTS=$2 GAP=$3 N=$4 C=$5
UPPORT=${UPPORT:-19181} GWPORT=${GWPORT:-19182}
# a previous gateway may still be draining on the port: wait (up to 60 s) until nothing listens on it
for _ in $(seq 120); do python3 -c "import socket,sys; s=socket.socket(); s.settimeout(0.2); sys.exit(0 if s.connect_ex(('127.0.0.1', $GWPORT)) == 0 else 1)" || break; sleep 0.5; done
unset HTTPS_PROXY https_proxy HTTP_PROXY http_proxy ALL_PROXY all_proxy NO_PROXY no_proxy
KEY="bench-$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')"
GWKEY="gw-$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')"
B121_BENCH_KEY=$KEY "$BENCHPY" -I "$HERE/../bench/sse_upstream.py" --host 127.0.0.1 --port $UPPORT \
  --events "$EVENTS" --gap-ms "$GAP" >"$OUT/$LABEL.up.log" 2>&1 &
UP=$!
( cd "$GW" && exec env -i PATH="$PATH" HOME="$HOME" PYTHONPATH="$GW" RV_HOST=127.0.0.1 RV_PORT=$GWPORT \
  WEB_CONCURRENCY=1 AMF_DEV_PROFILE=1 AMF_DEV_API_KEY="$GWKEY" AMF_DEV_ORG_ID=dev-org \
  AMF_DEV_PLAN_PATH="$GW/dev/plan.example.json" AMF_DEV_MODELS=gpt-4o-2024-08-06 AMF_DEV_Q_SAFE=500 \
  AMF_DEV_AUDIT_DRAIN_RATE_PER_S=1000 AMF_DEV_AUDIT_BYTES_PER_RECORD=2765 AMF_DEV_BUDGET=unlimited \
  AMF_DEV_PROVIDER_URL="http://127.0.0.1:$UPPORT/v1" AMF_DEV_PROVIDER_API_KEY="$KEY" \
  "$GWPY" -m gateway_v2.edge.serve >"$OUT/$LABEL.gw.log" 2>&1 ) &
GWP=$!
trap 'kill $UP $GWP 2>/dev/null' EXIT
UPOK=0
for _ in $(seq 400); do curl -s --noproxy '*' "http://127.0.0.1:$GWPORT/livez" >/dev/null 2>&1 && { UPOK=1; break; }; sleep 0.3; done
[ "$UPOK" = 1 ] || { echo "gateway not live after 120 s; aborting $LABEL" >&2; exit 3; }
cpu() { local t=0; for p in $(pgrep -P $GWP) $GWP; do t=$((t + $(awk '{print $14+$15}' /proc/$p/stat))); done; echo $t; }
export DEVPATH_GATEWAY_KEY=$GWKEY
URL="http://127.0.0.1:$GWPORT/v1/chat/completions"
# warm-up
"$BENCHPY" -I "$HERE/load_client.py" --url "$URL" --requests $((C*2)) --concurrency "$C" >"$OUT/$LABEL.warmup.json"
# idle baseline over 10 s
I0=$(cpu); sleep 10; I1=$(cpu)
T0=$(cpu); W0=$(date +%s.%N)
"$BENCHPY" -I "$HERE/load_client.py" --url "$URL" --requests "$N" --concurrency "$C" >"$OUT/$LABEL.load.json"
T1=$(cpu); W1=$(date +%s.%N)
HZ=$(getconf CLK_TCK)
python3 - "$LABEL" "$EVENTS" "$GAP" "$N" "$C" "$I0" "$I1" "$T0" "$T1" "$W0" "$W1" "$HZ" "$OUT/$LABEL.load.json" <<'PY' | tee "$OUT/$LABEL.result.json"
import json, sys
label, ev, gap, n, c, i0, i1, t0, t1, w0, w1, hz, lf = sys.argv[1:]
hz = int(hz); load = json.load(open(lf))
wall = float(w1) - float(w0)
idle_rate = (int(i1) - int(i0)) / hz / 10.0          # CPU s per wall s while idle
busy = (int(t1) - int(t0)) / hz
net = busy - idle_rate * wall
ok = load["ok"]
print(json.dumps({"label": label, "events": int(ev), "gap_ms": float(gap), "requests": int(n),
                  "concurrency": int(c), "qualified_ok": ok, "load": load, "wall_s": round(wall, 2),
                  "gateway_cpu_s": round(busy, 3), "idle_cpu_s_per_s": round(idle_rate, 4),
                  "cpu_ms_per_qualified_request": round(net / max(ok, 1) * 1000, 3),
                  "cpu_us_per_event": round(net / max(ok, 1) / int(ev) * 1e6, 1),
                  "gateway_util": round(busy / wall, 3)}))
PY
grep -m3 -iE "admission|concurrency=|DEV PROFILE" "$OUT/$LABEL.gw.log" | cut -c1-240 > "$OUT/$LABEL.boot_excerpt.txt" || true
