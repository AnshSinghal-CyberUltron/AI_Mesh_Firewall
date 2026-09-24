#!/usr/bin/env bash
# Build a TensorRT plan for Triton inside the given tritonserver image, benchmark it with trtexec
# (GPU-only timing), and lay out the model repository.
#   26.05-py3 = TRT 10.16.1.11 (same TRT as the in-process ORT TRT EP): weakly-typed --fp16 from model.fp32.onnx
#   26.08-py3 = TRT 11.2.1.2: strongly typed only (no --fp16 flag) -> built from model.fp16.onnx
# usage: triton_build.sh <model-name> <triton-name> <tag: 26.05|26.08> <Ws for trtexec timing>
set -uo pipefail
M=$1; TN=$2; TAG=$3; WS=${4:-"1 2 3 4 7 16"}
IMG=nvcr.io/nvidia/tritonserver:$TAG-py3
if [[ $TAG == 26.05 ]]; then SRC=model.fp32.onnx; PREC=--fp16; SUB=trt10_2605; else SRC=model.fp16.onnx; PREC=; SUB=trt11_2608; fi
O=~/gb/out/B/$M/$SUB; R=~/gb/triton/models_$TAG; mkdir -p $O $R/$TN/1 ~/gb/triton/build
S=input_ids:1x512,attention_mask:1x512
P=input_ids:16x512,attention_mask:16x512
X=input_ids:64x512,attention_mask:64x512
t0=$(date +%s.%N)
sudo docker run --rm --gpus all -v $HOME/gb:/gb $IMG trtexec --onnx=/gb/onnx/$M/$SRC $PREC \
  --saveEngine=/gb/triton/models_$TAG/$TN/1/model.plan --minShapes=$S --optShapes=$P --maxShapes=$X \
  --memPoolSize=workspace:4096M --timingCacheFile=/gb/triton/build/${TN}_$TAG.tcache --skipInference > $O/trtexec_build.log 2>&1
t1=$(date +%s.%N)
echo "{\"model\":\"$M\",\"image\":\"$IMG\",\"onnx\":\"$SRC\",\"flags\":\"$PREC\",\"build_seconds\":$(echo "$t1 - $t0" | bc)}" > $O/trtexec_build_time.json
EXTRA=""; [[ $TAG == 26.05 ]] && EXTRA="--noDataTransfers --useCudaGraph"
for W in $WS; do
  sudo docker run --rm --gpus all -v $HOME/gb:/gb $IMG trtexec --loadEngine=/gb/triton/models_$TAG/$TN/1/model.plan \
    --shapes=input_ids:${W}x512,attention_mask:${W}x512 --warmUp=1000 --iterations=2000 --duration=0 --avgRuns=1 $EXTRA \
    --percentile=50,90,99 --exportTimes=/gb/out/B/$M/$SUB/trtexec_times_W$W.json > $O/trtexec_W$W.log 2>&1
done
sudo chown -R $USER $HOME/gb/out $HOME/gb/triton
echo TRITON_BUILD_DONE $M $TAG
