#!/usr/bin/env bash
# Build the Triton model repository for the triton_grpc guard backend: one TensorRT plan per PG2
# model, built by trtexec INSIDE the Triton image (so plan TRT == server TRT, 10.16.1.11 for 26.05),
# fp16 from the fp32 ONNX export, dynamic batch AND sequence so rvproto's sequence buckets batch
# together per bucket. Dynamic batcher + one instance per GPU. Plans are GPU-arch specific (L4=sm89).
# Usage: triton_build.sh <triton-image>   env: TRITON_MAX_BATCH (64) TRITON_QUEUE_US (1000)
set -euxo pipefail
IMG=${1:-nvcr.io/nvidia/tritonserver:26.05-py3}
RV=$HOME/rv
REPO=$RV/triton/models
MAXB=${TRITON_MAX_BATCH:-64}
QUS=${TRITON_QUEUE_US:-1000}
NGPU=$(nvidia-smi -L | grep -c '^GPU ')
INSTANCES=""
for ((g = 0; g < NGPU; g++)); do INSTANCES+="{ count: 1 kind: KIND_GPU gpus: [ $g ] },"; done
mkdir -p "$RV/triton/build"
for M in 22M 86M; do
  N=pg2-$(echo "$M" | tr 'M' 'm')
  mkdir -p "$REPO/$N/1"
  if [ ! -s "$REPO/$N/1/model.plan" ]; then
    sudo docker run --rm --gpus all -v "$RV:/rv" "$IMG" trtexec --onnx="/rv/models/pg2-$M.onnx" --fp16 \
      --saveEngine="/rv/triton/models/$N/1/model.plan" \
      --minShapes=input_ids:1x16,attention_mask:1x16 \
      --optShapes=input_ids:16x512,attention_mask:16x512 \
      --maxShapes=input_ids:${MAXB}x512,attention_mask:${MAXB}x512 \
      --memPoolSize=workspace:4096M --timingCacheFile="/rv/triton/build/$N.tcache" --skipInference \
      > "$RV/triton/build/$N.trtexec.log" 2>&1
  fi
  cat > "$REPO/$N/config.pbtxt" <<CFG
name: "$N"
platform: "tensorrt_plan"
max_batch_size: $MAXB
input [
  { name: "input_ids" data_type: TYPE_INT64 dims: [ -1 ] },
  { name: "attention_mask" data_type: TYPE_INT64 dims: [ -1 ] }
]
output [ { name: "logits" data_type: TYPE_FP32 dims: [ 2 ] } ]
instance_group [ ${INSTANCES%,} ]
dynamic_batching { max_queue_delay_microseconds: $QUS }
CFG
done
sudo chown -R "$USER" "$RV/triton"
sha256sum "$REPO"/*/1/model.plan
echo TRITON_BUILD_DONE
