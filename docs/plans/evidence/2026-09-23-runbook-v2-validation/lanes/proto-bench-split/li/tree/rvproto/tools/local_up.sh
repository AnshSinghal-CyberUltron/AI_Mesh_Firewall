#!/usr/bin/env bash
# Local functional-test stack: throwaway redis (docker, :16379), provider (synthprov if
# PROVIDER=synthprov else tools/devprov.py) on :18080, rvproto launcher on :8400.
# GUARD_NODE=1: split topology on loopback: a guard-only node (owners on TCP, GUARD_NODE_LISTEN,
# default tcp://127.0.0.1:7070) plus a gateway-only launcher (RV_GUARD_TOPOLOGY=remote).
# Usage: tools/local_up.sh <logdir> [extra env assignments...]
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
LOG=${1:?logdir}; shift || true
mkdir -p "$LOG"
RUN=${RV_RUN_DIR:-$HERE/.run}; mkdir -p "$RUN"
source "$HERE/tools/env_local.sh"
for kv in "$@"; do export "$kv"; done
docker inspect rv-proto-redis >/dev/null 2>&1 || docker run -d --rm --name rv-proto-redis \
  -p 127.0.0.1:16379:6379 redis:7.4-alpine redis-server --save "" --appendonly no >/dev/null
"$HERE/.venv/bin/python" "$HERE/tools/seed.py" "$RV_REDIS_URL" --flush > "$LOG/seed.json"
CANARY="alice.canary@example.com,AKIAQYLPMN5HHHFPZAM2,4111111111111111"
if [[ "${PROVIDER:-devprov}" == "synthprov" ]]; then
  SYN=${SYNTHPROV_BIN:?set SYNTHPROV_BIN}
  setsid nohup $SYN ${SYNTHPROV_ARGS:-} > "$LOG/provider.log" 2>&1 &
  echo $! > "$RUN/provider.pid"
else
  setsid nohup "$HERE/.venv/bin/python" "$HERE/tools/devprov.py" --port 18080 \
    --record "$LOG/provider-records.jsonl" --canary "$CANARY" > "$LOG/provider.log" 2>&1 &
  echo $! > "$RUN/provider.pid"
fi
cd "$HERE"
if [[ "${GUARD_NODE:-0}" == "1" ]]; then
  RV_GUARD_OWNER_LISTEN=${GUARD_NODE_LISTEN:-tcp://127.0.0.1:7070} \
    setsid nohup .venv/bin/python -m rvproto.serve --guard-node > "$LOG/guard-node.log" 2>&1 &
  echo $! > "$RUN/guard-node.pid"
  export RV_GUARD_TOPOLOGY=remote RV_GUARD_OWNER_ADDRS=${RV_GUARD_OWNER_ADDRS:-127.0.0.1:7070}
fi
setsid nohup .venv/bin/python -m rvproto.serve > "$LOG/rvproto.log" 2>&1 &
echo $! > "$RUN/rvproto.pid"
for i in $(seq 1 240); do
  if curl -sf -m 3 "localhost:$RV_PORT/readyz" > "$LOG/readyz.json" 2>/dev/null; then
    # every worker must be ready: probe until N distinct workers answered ready
    n=${WEB_CONCURRENCY:-1}; seen=""
    for j in $(seq 1 60); do
      w=$(curl -s -m 3 "localhost:$RV_PORT/readyz" | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin); print(d["worker"] if d["ready"] else "")
except Exception:
    print("")')
      [[ -n "$w" && ! " $seen " =~ " $w " ]] && seen="$seen $w"
      [[ $(wc -w <<<"$seen") -ge $n ]] && break
      sleep 0.5
    done
    echo "ready workers:$seen"; exit 0
  fi
  sleep 1
done
echo "rvproto not ready"; tail -50 "$LOG/rvproto.log"; exit 1
