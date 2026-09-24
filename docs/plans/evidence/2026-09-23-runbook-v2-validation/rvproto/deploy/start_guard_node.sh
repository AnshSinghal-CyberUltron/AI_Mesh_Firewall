#!/usr/bin/env bash
# Split topology, GUARD side (G2 node from image family rv-proto-unit): one guard-owner process
# per GPU (ORT TensorRT EP, exact 1x512 engine, batch 1) serving REMOTE gateway workers over TCP.
# No HTTP workers run here. Usage: start_guard_node.sh [22M|86M]
# Env (all optional):
#   RV_GUARD_OWNER_LISTEN  default tcp://<this VM's internal IP>:7070 -> owner i listens on 7070+i
#   AMF_TARGET_P99_MS      default 20; MUST equal the gateway nodes' value (the owner-side queue
#                          cap is pool_size(GUARD) = measured tokens/s x this target)
#   RV_GUARD_BATCH / RV_GUARD_WAIT_US / RV_GUARD_SEQ_BUCKETS (guard-bench: 1 / 0 / 512)
# SECURITY: the owner TCP protocol is UNAUTHENTICATED and unencrypted (prototype). Bind it to the
# VPC-internal address only (the default) and never add a firewall rule exposing 7070+ beyond the
# VPC; GCP's default-allow-internal (10.128.0.0/9) is what admits gateway nodes. Every frame is
# still validated (size, window geometry, token ids < vocabulary) before it reaches the engine,
# and each owner's queue is capped, so a stray client can waste capacity but not crash an owner.
# Stop: bash ~/rv/rvproto/deploy/stop_unit.sh
set -euo pipefail
SIZE=${1:-22M}
RV=$HOME/rv
source "$RV/rvproto/deploy/gpuenv.sh"
ulimit -n "$(ulimit -Hn)"   # the contract derives connection budgets from RLIMIT_NOFILE
export RV_GATEWAY_V2_PATH=$RV/vendor
export RV_GUARD_BACKEND=local_gpu
export RV_GUARD_BATCH=${RV_GUARD_BATCH:-1} RV_GUARD_WAIT_US=${RV_GUARD_WAIT_US:-0}
export RV_GUARD_SEQ_BUCKETS=${RV_GUARD_SEQ_BUCKETS:-512}
export RV_GUARD_MODEL=$RV/models/pg2-$SIZE.onnx
export RV_GUARD_TOKENIZER=$RV/models/tokenizer-$SIZE.json
export RV_TRT_CACHE_DIR=${RV_TRT_CACHE_DIR:-$RV/trtcache}
export AMF_TARGET_P99_MS=${AMF_TARGET_P99_MS:-20}
# the VPC-internal address of nic0 (metadata server), not docker0's bridge address
IP=$(curl -sf -m 2 -H 'Metadata-Flavor: Google' \
  http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/ip || hostname -I | awk '{print $1}')
export RV_GUARD_OWNER_LISTEN=${RV_GUARD_OWNER_LISTEN:-tcp://$IP:7070}
export RV_GUARD_SOCKET_DIR=${RV_GUARD_SOCKET_DIR:-$RV/run/guard}
export RV_METRICS_DIR=${RV_METRICS_DIR:-$RV/metrics}
mkdir -p "$RV_METRICS_DIR" "$RV/logs" "$RV/run"
rm -f "$RV_METRICS_DIR"/owner-*.json "$RV_METRICS_DIR"/worker-*.json
cd "$RV/rvproto"
# one process builds/loads the TRT engine cache for every GPU before the owners start
"$RV/venv/bin/python" tools/prebuild_trt.py > "$RV/logs/prebuild_trt.json" 2> "$RV/logs/prebuild_trt.err"
cat "$RV/logs/prebuild_trt.json"
setsid nohup "$RV/venv/bin/python" -m rvproto.serve --guard-node > "$RV/logs/guard-node.log" 2>&1 &
echo $! > "$RV/run/rvproto.pid"
NGPU=$(nvidia-smi -L | grep -c '^GPU ')
for i in $(seq 1 900); do
  n=$(ls "$RV_GUARD_SOCKET_DIR"/guard-*.sock.ready 2>/dev/null | wc -l)
  if [[ $n -ge $NGPU ]]; then
    ADDRS=$("$RV/venv/bin/python" -c 'import glob,json,sys
print(",".join(e for p in sorted(glob.glob(sys.argv[1] + "/guard-*.sock.ready"))
               for e in json.load(open(p))["endpoints"] if not e.startswith("unix:")))' "$RV_GUARD_SOCKET_DIR")
    echo "GUARD NODE READY after ${i}s: $n owners; RV_GUARD_OWNER_ADDRS entries: $ADDRS"
    exit 0
  fi
  sleep 1
done
echo "NOT READY"; tail -40 "$RV/logs/guard-node.log"; exit 1
