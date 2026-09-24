#!/usr/bin/env bash
# Exp A throughput: micro-batcher grid for one model. usage: driver_a_mb.sh <model> <opt-batch> <Ws> <Bs> <waits_us>
set -uo pipefail
M=$1; OB=$2; WS=$3; BS=$4; WAITS=$5
source ~/gb/gpuenv.sh; cd ~/gb
O=~/gb/out/A/$M/mb; mkdir -p $O
F="--onnx-dir $HOME/gb/onnx/$M --tok-dir $HOME/gb/models/$M --corpus $HOME/gb/corpus"
CAP=~/gb/out/A/$M/capacity_trt_fp16_dyn_opt$OB.json
nvidia-smi dmon -s pucvmet -d 1 -o T > $O/dmon_$(date +%s).log 2>&1 &
DMON=$!
for W in ${WS//,/ }; do
  # baseline: no cross-request batching (each request = one sess.run of its W windows)
  R=$(python scripts/plan_ladder.py $CAP $W $W)
  python scripts/microbatch_bench.py $F --backend trt_fp16_dyn --opt-batch $OB --W $W --B $W --wait-us 0 \
     --rates $R --dur 10 --warm 2 --stop-p99-ms 60 --out-dir $O/W${W}_nobatch > $O/W${W}_nobatch.log 2>&1
  for B in ${BS//,/ }; do
    R=$(python scripts/plan_ladder.py $CAP $W $B)
    for WT in ${WAITS//,/ }; do
      python scripts/microbatch_bench.py $F --backend trt_fp16_dyn --opt-batch $OB --W $W --B $B --wait-us $WT \
        --rates $R --dur 10 --warm 2 --stop-p99-ms 60 --out-dir $O/W${W}_B${B}_w${WT} > $O/W${W}_B${B}_w${WT}.log 2>&1
    done
  done
done
kill $DMON
echo DRIVER_A_MB_DONE $M
