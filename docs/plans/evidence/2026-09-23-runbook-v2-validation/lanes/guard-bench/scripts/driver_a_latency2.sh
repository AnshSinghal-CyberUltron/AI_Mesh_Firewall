#!/usr/bin/env bash
# (v2: adds the 7-profile trt_fp16_multi backend)
# Exp A per-call latency + engine build/restart + closed-loop batch sweep for one model on one L4.
# usage: driver_a_latency.sh <model-name>
set -uo pipefail
M=$1
source ~/gb/gpuenv.sh
cd ~/gb
O=~/gb/out/A/$M; mkdir -p $O
ONNX=~/gb/onnx/$M; TOK=~/gb/models/$M; CORP=~/gb/corpus
nvidia-smi dmon -s pucvmet -d 1 -o T > $O/dmon.log 2>&1 &
DMON=$!
[ -f $ONNX/model.fp16.onnx ] || python scripts/make_fp16.py $ONNX $TOK $CORP > $O/make_fp16.log 2>&1
cp $ONNX/fp16_report.json $O/ 2>/dev/null
F="--onnx-dir $ONNX --tok-dir $TOK --corpus $CORP"
python scripts/bench_latency_gpu.py $F --backend trt_fp16_static --cold --out $O/lat_trt_fp16_static.json > $O/lat_trt_fp16_static.log 2>&1
for ob in 16 4; do
  python scripts/bench_latency_gpu.py $F --backend trt_fp16_dyn --opt-batch $ob --cold --out $O/lat_trt_fp16_dyn_opt$ob.json > $O/lat_trt_fp16_dyn_opt$ob.log 2>&1
done
python scripts/bench_latency_gpu.py $F --backend trt_fp16_multi --cold --out $O/lat_trt_fp16_multi.json > $O/lat_trt_fp16_multi.log 2>&1
python scripts/bench_latency_gpu.py $F --backend cuda_fp32 --out $O/lat_cuda_fp32.json > $O/lat_cuda_fp32.log 2>&1
python scripts/bench_latency_gpu.py $F --backend cuda_fp16 --out $O/lat_cuda_fp16.json > $O/lat_cuda_fp16.log 2>&1
# warm restarts from a populated engine cache (fresh process each time)
for i in 1 2 3; do
  python scripts/bench_latency_gpu.py $F --backend trt_fp16_static --restart-probe 1 512 --out $O/restart_static_1x512_$i.json >> $O/restart.log 2>&1
  python scripts/bench_latency_gpu.py $F --backend trt_fp16_dyn --opt-batch 16 --restart-probe 2 512 --out $O/restart_dyn16_$i.json >> $O/restart.log 2>&1
done
python scripts/capacity_probe.py $F --backend trt_fp16_multi --out $O/capacity_trt_fp16_multi.json > $O/capacity_trt_fp16_multi.log 2>&1
for ob in 16 4; do
  python scripts/capacity_probe.py $F --backend trt_fp16_dyn --opt-batch $ob --out $O/capacity_trt_fp16_dyn_opt$ob.json > $O/capacity_trt_fp16_dyn_opt$ob.log 2>&1
done
python scripts/capacity_probe.py $F --backend cuda_fp16 --batches 1,2,4,8,16,32,64 --secs 3 --out $O/capacity_cuda_fp16.json > $O/capacity_cuda_fp16.log 2>&1
kill $DMON
echo DRIVER_A_LATENCY_DONE $M
