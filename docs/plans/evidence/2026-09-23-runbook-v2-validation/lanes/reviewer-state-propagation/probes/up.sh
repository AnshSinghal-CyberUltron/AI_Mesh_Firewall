#!/usr/bin/env bash
# reviewer-state-propagation: start an ISOLATED rvproto stack (own redis container, own ports).
# Never touches rv-proto-redis/:16379/:8400 (other agents) or the compose stack.
#   up.sh <logdir> [ENV=VAL ...]
# Ports: redis 26379 (container sp-rsp-redis), devprov 18093, rvproto 8493.
# RV_REDIS_URL may be overridden (e.g. to point at the fault proxy on 26380).
set -euo pipefail
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
EV=$SP/evidence/reviewer-state-propagation
RV=${RVPROTO_DIR:-$EV/rvproto-frozen1}   # private, manifest-verified copy of rvproto-frozen-1
PYBIN=$SP/rvproto/.venv/bin/python
LOG=${1:?logdir}; shift || true
mkdir -p "$LOG"
export RV_RUN_DIR=$EV/run
mkdir -p "$RV_RUN_DIR"
export RV_PORT=8493
export RV_PROVIDER_URL=http://127.0.0.1:18093
export RV_REDIS_URL=redis://127.0.0.1:26379/0
export RV_GUARD_BACKEND=local_cpu
export RV_GUARD_TOPOLOGY=owner
export RV_GUARD_MODEL=$RV/models/pg2-22M.onnx
export RV_GUARD_TOKENIZER=$SP/models/Llama-Prompt-Guard-2-22M/tokenizer.json
export RV_GUARD_CPU_THREADS=2
export RV_GUARD_DEADLINE_MS=5000
export AMF_TARGET_P99_MS=2000
export WEB_CONCURRENCY=4
export RV_ADMIN_HOOKS=1
export RV_METRICS_DIR=$LOG/metrics
SEED_URL=redis://127.0.0.1:26379/0
for kv in "$@"; do export "$kv"; done
mkdir -p "$RV_METRICS_DIR"
docker inspect sp-rsp-redis >/dev/null 2>&1 || docker run -d --rm --name sp-rsp-redis \
  -p 127.0.0.1:26379:6379 redis:7.4-alpine redis-server --save "" --appendonly no >/dev/null
for i in $(seq 1 50); do docker exec sp-rsp-redis redis-cli ping 2>/dev/null | grep -q PONG && break; sleep 0.2; done
if [[ "${NO_SEED:-0}" != "1" ]]; then
  "$PYBIN" "$RV/tools/seed.py" "$SEED_URL" --flush > "$LOG/seed.json"
fi
if [[ ! -f "$RV_RUN_DIR/provider.pid" ]] || ! kill -0 "$(cat "$RV_RUN_DIR/provider.pid")" 2>/dev/null; then
  setsid nohup "$PYBIN" "$RV/tools/devprov.py" --port 18093 \
    --record "$LOG/provider-records.jsonl" --canary "alice.canary@example.com,AKIAQYLPMN5HHHFPZAM2" \
    > "$LOG/provider.log" 2>&1 &
  echo $! > "$RV_RUN_DIR/provider.pid"
fi
cd "$RV"
env | grep -E '^(RV_|AMF_|WEB_)' | sort > "$LOG/env.txt"
( cd "$RV" && sed -n '/^MANIFEST$/,/^ARTIFACTS$/p' VERSION | sed '1d;$d' | sha256sum -c --quiet && echo "code=$RV manifest_ok $(head -1 VERSION)" ) > "$LOG/code-version.txt" 2>&1
if [[ "${SIGHUP_DEFAULT:-0}" == "1" ]]; then
  # production-like: SIGHUP at its default disposition (nohup would make it SIG_IGN for every child)
  setsid "$PYBIN" -c 'import os,signal,sys; signal.signal(signal.SIGHUP, signal.SIG_DFL); os.execv(sys.executable, [sys.executable, "-m", "rvproto.serve"])' > "$LOG/rvproto.log" 2>&1 < /dev/null &
else
  setsid nohup "$PYBIN" -m rvproto.serve > "$LOG/rvproto.log" 2>&1 &
fi
echo $! > "$RV_RUN_DIR/rvproto.pid"
n=$WEB_CONCURRENCY
for i in $(seq 1 240); do
  if curl -sf -m 3 "localhost:$RV_PORT/readyz" > /dev/null 2>&1; then
    seen=""
    for j in $(seq 1 120); do
      w=$(curl -s -m 3 "localhost:$RV_PORT/readyz" | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin); print(d["worker"] if d["ready"] else "")
except Exception:
    print("")')
      [[ -n "$w" && ! " $seen " =~ " $w " ]] && seen="$seen $w"
      [[ $(wc -w <<<"$seen") -ge $n ]] && break
      sleep 0.25
    done
    echo "ready workers:$seen"; exit 0
  fi
  sleep 1
done
echo "rvproto not ready"; tail -30 "$LOG/rvproto.log"; exit 1
