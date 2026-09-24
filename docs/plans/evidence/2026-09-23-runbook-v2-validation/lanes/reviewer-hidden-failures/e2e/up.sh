#!/usr/bin/env bash
# usage: up.sh <logdir> [ENV=val ...]   starts synthprov + rvproto (bench source) against the reviewer redis
set -euo pipefail
source "$(dirname "$0")/env.sh"
LOG=${1:?logdir}; shift || true
mkdir -p "$LOG"; RUN=$EV/e2e/.run; mkdir -p "$RUN"
for kv in "$@"; do export "$kv"; done
export RV_METRICS_DIR=$LOG/metrics; mkdir -p "$RV_METRICS_DIR"
(cd "$RVSRC" && "$PY" tools/seed.py "$RV_REDIS_URL" --flush > "$LOG/seed.json")
setsid nohup "$SP/harness/bin/synthprov" -listen 127.0.0.1:47180 -record "$LOG/prov-records.jsonl" -ttft ${SYN_TTFT:-50ms} -itl ${SYN_ITL:-10ms} -sample-mod 1 > "$LOG/provider.log" 2>&1 &
echo $! > "$RUN/provider.pid"
cd "$RVSRC"; setsid nohup "$PY" -m rvproto.serve > "$LOG/rvproto.log" 2>&1 &
echo $! > "$RUN/rvproto.pid"
for i in $(seq 1 180); do
  if curl -sf -m 3 "localhost:$RV_PORT/readyz" > "$LOG/readyz.json" 2>/dev/null; then echo "ready after ${i}s"; exit 0; fi
  sleep 1
done
echo "not ready"; tail -30 "$LOG/rvproto.log"; exit 1
