#!/usr/bin/env bash
# cProfile of develop's dev-profile gateway serving 4 paced 200-event streams, one at a time.
# Usage: GW=<wt>/gateway_v2 GWPY=<venv-ci python> BENCHPY=<bench venv python> PROF_OUT=/tmp/gw.prof ./profile_run.sh
set -uo pipefail
EV=$(cd "$(dirname "$0")/.." && pwd)
GW=${GW:?}; GWPY=${GWPY:?}; BPY=${BENCHPY:?}
UPPORT=19281 GWPORT=19282
unset HTTPS_PROXY https_proxy HTTP_PROXY http_proxy
KEY="bench-$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')"; GWKEY="gw-$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')"
B121_BENCH_KEY=$KEY $BPY -I $EV/bench/sse_upstream.py --port $UPPORT --events 200 --gap-ms 20 > /tmp/prof_up.log 2>&1 &
UP=$!
( cd $GW && exec env -i PATH="$PATH" HOME="$HOME" PYTHONPATH="$GW" RV_HOST=127.0.0.1 RV_PORT=$GWPORT WEB_CONCURRENCY=1 \
  AMF_DEV_PROFILE=1 AMF_DEV_API_KEY="$GWKEY" AMF_DEV_ORG_ID=dev-org AMF_DEV_PLAN_PATH="$GW/dev/plan.example.json" \
  AMF_DEV_MODELS=gpt-4o-2024-08-06 AMF_DEV_Q_SAFE=500 AMF_DEV_AUDIT_DRAIN_RATE_PER_S=1000 AMF_DEV_AUDIT_BYTES_PER_RECORD=2765 \
  AMF_DEV_BUDGET=unlimited AMF_DEV_PROVIDER_URL="http://127.0.0.1:$UPPORT/v1" AMF_DEV_PROVIDER_API_KEY="$KEY" \
  $GWPY -m cProfile -o ${PROF_OUT:-/tmp/gw.prof} -m gateway_v2.edge.serve > /tmp/prof_gw.log 2>&1 ) &
GWP=$!
for _ in $(seq 400); do curl -s --noproxy '*' "http://127.0.0.1:$GWPORT/livez" >/dev/null 2>&1 && break; sleep 0.3; done
DEVPATH_GATEWAY_KEY=$GWKEY $BPY -I $EV/devpath/load_client.py --url "http://127.0.0.1:$GWPORT/v1/chat/completions" --requests 4 --concurrency 1
kill -INT $GWP; sleep 6; kill $UP
