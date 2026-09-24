#!/usr/bin/env bash
# Split topology, GATEWAY side (C4 node, see gateway_setup.sh): HTTP workers only; every guard
# window goes over TCP to the owners on the guard nodes (least outstanding windows per request;
# owner unreachable/dead/silent -> UNAVAILABLE -> posture, never clean; reconnect with backoff).
# Usage: RV_GUARD_OWNER_ADDRS=10.a.b.c:7070,10.a.b.c:7071,... RV_PROVIDER_URL=http://<synthprov>:8080 \
#          [RV_SEED=1] bash ~/rv/rvproto/deploy/start_gateway_node.sh [22M|86M]
# Env (optional):
#   RV_GUARD_FLEET_WORKERS  gateway workers fleet-wide that share these owners (each worker's guard
#                           bound = its share of the owners' measured rate). Default: this node's
#                           workers, which is right only for ONE gateway node; declare it otherwise
#                           (the owners' own queue caps still bound overload, as 503 sheds).
#   RV_REDIS_URL            default the local redis; several gateway nodes must share ONE store
#   RV_SEED=1               flush + seed keys/plans into that store (on ONE node of a fleet only)
#   AMF_TARGET_P99_MS       default 20 (must equal the guard nodes'); WEB_CONCURRENCY unset => contract
# The guard nodes must be READY first (start_guard_node.sh): workers become ready only after every
# listed owner has said hello (warm engine, pinned model hash).
# Stop: bash ~/rv/rvproto/deploy/stop_unit.sh
set -euo pipefail
SIZE=${1:-22M}
RV=$HOME/rv
ulimit -n "$(ulimit -Hn)"
export RV_GATEWAY_V2_PATH=$RV/vendor
export RV_GUARD_TOPOLOGY=remote
export RV_GUARD_OWNER_ADDRS=${RV_GUARD_OWNER_ADDRS:?set RV_GUARD_OWNER_ADDRS=host:port,...}
export RV_GUARD_TOKENIZER=$RV/models/tokenizer-$SIZE.json
export RV_GUARD_MODEL_SHA256=${RV_GUARD_MODEL_SHA256:-$(cut -d' ' -f1 "$RV/models/pg2-$SIZE.onnx.sha256")}
export RV_GUARD_SEQ_BUCKETS=${RV_GUARD_SEQ_BUCKETS:-512}  # padded-cost accounting = the guard nodes'
export RV_REDIS_URL=${RV_REDIS_URL:-redis://127.0.0.1:6379/0}
export RV_PROVIDER_URL=${RV_PROVIDER_URL:?set RV_PROVIDER_URL}
export AMF_TARGET_P99_MS=${AMF_TARGET_P99_MS:-20}
export RV_METRICS_DIR=${RV_METRICS_DIR:-$RV/metrics}
mkdir -p "$RV_METRICS_DIR" "$RV/logs" "$RV/run"
rm -f "$RV_METRICS_DIR"/worker-*.json
cd "$RV/rvproto"
[[ -n "${RV_SEED:-}" ]] && "$RV/venv/bin/python" tools/seed.py "$RV_REDIS_URL" --flush >/dev/null
setsid nohup "$RV/venv/bin/python" -m rvproto.serve > "$RV/logs/rvproto-gateway.log" 2>&1 &
echo $! > "$RV/run/rvproto.pid"
# ready = EVERY worker answered /readyz 200 (SO_REUSEPORT spreads fresh connections)
for i in $(seq 1 600); do
  n=$(curl -sf -m 2 "localhost:${RV_PORT:-8400}/_rv/contract" | "$RV/venv/bin/python" -c 'import json,sys; print(json.load(sys.stdin)["workers"])' 2>/dev/null || echo 0)
  if [[ $n -gt 0 ]]; then
    seen=$(for j in $(seq 1 $((n * 8))); do curl -s -m 2 "localhost:${RV_PORT:-8400}/readyz"; echo; done \
      | "$RV/venv/bin/python" -c 'import json,sys
print(len({d["worker"] for d in (json.loads(l) for l in sys.stdin if l.strip()) if d.get("ready")}))' 2>/dev/null || echo 0)
    if [[ $seen -ge $n ]]; then echo "GATEWAY NODE READY after ${i}s: $seen/$n workers"; exit 0; fi
  fi
  sleep 1
done
echo "NOT READY"; curl -s -m 2 "localhost:${RV_PORT:-8400}/readyz"; echo; tail -40 "$RV/logs/rvproto-gateway.log"; exit 1
