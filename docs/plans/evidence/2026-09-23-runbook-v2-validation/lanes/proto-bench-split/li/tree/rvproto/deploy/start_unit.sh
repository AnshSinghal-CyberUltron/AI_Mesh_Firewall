#!/usr/bin/env bash
# Start rvproto on a unit. Usage: start_unit.sh <local_gpu|triton_grpc|local_cpu> [22M|86M]
# Env overrides (all optional): RV_REDIS_URL (default local redis), RV_PROVIDER_URL (required),
# AMF_TARGET_P99_MS (default 20), WEB_CONCURRENCY (unset => contract-derived workers),
# RV_GUARD_BATCH / RV_GUARD_WAIT_US / RV_GUARD_SEQ_BUCKETS / RV_GUARD_DEADLINE_MS, RV_PORT (8400),
# RV_GUARD_TOPOLOGY (local_gpu default owner: one guard-owner process per GPU; in_process).
set -euo pipefail
BACKEND=${1:?backend}; SIZE=${2:-22M}  # guard-bench: 86M is not viable on a 10 ms guard budget
RV=$HOME/rv
source "$RV/rvproto/deploy/gpuenv.sh"
ulimit -n "$(ulimit -Hn)"   # the contract derives connection budgets from RLIMIT_NOFILE
export RV_GATEWAY_V2_PATH=$RV/vendor
export RV_GUARD_BACKEND=$BACKEND
if [[ $BACKEND == local_gpu ]]; then  # guard-bench recommendation: serial batch-1 on one 1x512 engine
  export RV_GUARD_BATCH=${RV_GUARD_BATCH:-1} RV_GUARD_WAIT_US=${RV_GUARD_WAIT_US:-0}
  export RV_GUARD_SEQ_BUCKETS=${RV_GUARD_SEQ_BUCKETS:-512}
  # guard-bench: exactly ONE process may own each GPU session (N per-worker sessions on one L4
  # fall from 466 to 186-270 windows/s); workers hand windows to it over a Unix socket.
  # RV_GUARD_TOPOLOGY=in_process restores the PROTO_SPEC per-worker variant.
  export RV_GUARD_TOPOLOGY=${RV_GUARD_TOPOLOGY:-owner}
fi
export RV_GUARD_TOPOLOGY=${RV_GUARD_TOPOLOGY:-in_process}
export RV_GUARD_SOCKET_DIR=${RV_GUARD_SOCKET_DIR:-$RV/run/guard}
export RV_GUARD_MODEL=$RV/models/pg2-$SIZE.onnx
export RV_GUARD_TOKENIZER=$RV/models/tokenizer-$SIZE.json
export RV_TRITON_MODEL=pg2-$(echo "$SIZE" | tr 'M' 'm')
export RV_TRT_CACHE_DIR=${RV_TRT_CACHE_DIR:-$RV/trtcache}
export RV_REDIS_URL=${RV_REDIS_URL:-redis://127.0.0.1:6379/0}
export RV_PROVIDER_URL=${RV_PROVIDER_URL:?set RV_PROVIDER_URL}
export AMF_TARGET_P99_MS=${AMF_TARGET_P99_MS:-20}
# Deployment-declared per-worker RSS (GW03 AMF_PER_WORKER_RSS_MB; default 400). In-process
# guards load the model in EVERY worker; measured VmHWM on g2-standard-24 (evidence
# worker-rss.txt): local_gpu 86M 6.77 GB peak / 3.45 GB steady, 22M 2.51 / 1.75 GB,
# triton_grpc worker 0.32 GB. Workers start together, so the PEAK is declared. The
# undeclared default (400 MB) derived 18 workers and the kernel OOM-killed one at startup.
# Owner topology: workers hold no model (like triton_grpc workers); the owners' RSS is not
# modelled by GW03 and runs inside the utilization headroom (a declared deviation).
if [[ -z ${AMF_PER_WORKER_RSS_MB:-} && $BACKEND == local_gpu && $RV_GUARD_TOPOLOGY == in_process ]]; then
  if [[ $SIZE == 86M ]]; then export AMF_PER_WORKER_RSS_MB=7000; else export AMF_PER_WORKER_RSS_MB=2600; fi
fi
export RV_METRICS_DIR=${RV_METRICS_DIR:-$RV/metrics}
mkdir -p "$RV_METRICS_DIR" "$RV/logs" "$RV/run"
rm -f "$RV_METRICS_DIR"/worker-*.json
if [[ $BACKEND == triton_grpc ]]; then
  # one Triton instance per GPU actually present (the image may land on a different G2 shape)
  NGPU=$(nvidia-smi -L | grep -c '^GPU ')
  INST=""; for ((g = 0; g < NGPU; g++)); do INST+="{ count: 1 kind: KIND_GPU gpus: [ $g ] },"; done
  sed -i "s|^instance_group .*|instance_group [ ${INST%,} ]|" "$RV/triton/models/$RV_TRITON_MODEL/config.pbtxt"
  sudo docker rm -f rv-triton >/dev/null 2>&1 || true
  sudo docker run -d --name rv-triton --gpus all --net host --shm-size 2g --ulimit memlock=-1 \
    -v "$RV/triton/models:/models" "${TRITON_IMAGE:-nvcr.io/nvidia/tritonserver:26.05-py3}" \
    tritonserver --model-repository=/models --model-control-mode=explicit \
    --load-model="$RV_TRITON_MODEL" --log-verbose=0 >/dev/null
  for i in $(seq 1 300); do
    curl -sf "localhost:8000/v2/models/$RV_TRITON_MODEL/ready" >/dev/null && break; sleep 1
  done
fi
cd "$RV/rvproto"
if [[ $BACKEND == local_gpu ]]; then  # one process builds/loads the TRT engine cache for every GPU
  "$RV/venv/bin/python" tools/prebuild_trt.py > "$RV/logs/prebuild_trt.json" 2> "$RV/logs/prebuild_trt.err"
  cat "$RV/logs/prebuild_trt.json"
fi
[[ -n "${RV_SEED:-}" ]] && "$RV/venv/bin/python" tools/seed.py "$RV_REDIS_URL" --flush >/dev/null
setsid nohup "$RV/venv/bin/python" -m rvproto.serve > "$RV/logs/rvproto-$BACKEND.log" 2>&1 &
echo $! > "$RV/run/rvproto.pid"
# ready = EVERY worker answered /readyz 200 (SO_REUSEPORT spreads fresh connections)
for i in $(seq 1 900); do
  n=$(curl -sf -m 2 "localhost:${RV_PORT:-8400}/_rv/contract" | "$RV/venv/bin/python" -c 'import json,sys; print(json.load(sys.stdin)["workers"])' 2>/dev/null || echo 0)
  if [[ $n -gt 0 ]]; then
    seen=$(for j in $(seq 1 $((n * 8))); do curl -s -m 2 "localhost:${RV_PORT:-8400}/readyz"; echo; done \
      | "$RV/venv/bin/python" -c 'import json,sys
print(len({d["worker"] for d in (json.loads(l) for l in sys.stdin if l.strip()) if d.get("ready")}))' 2>/dev/null || echo 0)
    if [[ $seen -ge $n ]]; then echo "READY after ${i}s: $seen/$n workers"; exit 0; fi
  fi
  sleep 1
done
echo "NOT READY"; tail -40 "$RV/logs/rvproto-$BACKEND.log"; exit 1
