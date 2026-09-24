#!/usr/bin/env bash
# Exp A throughput grid for one model (v2, lean): per mix: nobatch on bucketed exact-shape engines, nobatch with every
# window as its own batch-1 call (serial1), then micro-batcher B x wait grids (full grid for W=2, corners for W=3/headline).
# usage: [GRID=lean] driver_a_mb2.sh <model>
set -uo pipefail
M=$1; BK=trt_fp16_bucketed
source ~/gb/gpuenv.sh; cd ~/gb
O=~/gb/out/A/$M/mb_$BK; mkdir -p $O
F="--onnx-dir $HOME/gb/onnx/$M --tok-dir $HOME/gb/models/$M --corpus $HOME/gb/corpus"
nvidia-smi dmon -s pucvmet -d 1 -o T > $O/dmon_$(date +%s).log 2>&1 &
DMON=$!
CAP=~/gb/out/A/$M/capacity_$BK.json
if [[ ! -f $CAP ]]; then
  python scripts/capacity_probe.py $F --backend $BK --batches 1,2,3,4,5,6,7,8,12,16,24,32,48,64 --secs 4 \
     --out $CAP > ${CAP%.json}.log 2>&1
fi
run() {  # $1 mix  $2 B  $3 wait_us  $4 outname  $5 backend
  local MX=$1 B=$2 WT=$3 NAME=$4 BE=$5 WARG MB PW
  if [[ $MX == headline ]]; then WARG="--W 2 --wmix headline"; PW=2; else WARG="--W $MX"; PW=$MX; fi
  MB=$B; [[ $B -lt 4 ]] && MB=4
  R=$(python scripts/plan_ladder.py $CAP $PW $B)
  python scripts/microbatch_bench.py $F --backend $BE --max-batch $MB $WARG --B $B --wait-us $WT --rates $R \
     --dur 10 --warm 2 --min-reqs 1000 --max-dur 30 --stop-p99-ms 40 --out-dir $O/$NAME > $O/$NAME.log 2>&1
}
for MX in 2 3 headline; do
  run $MX 1 0 W${MX}_nobatch $BK
  run $MX 1 0 W${MX}_serial1 trt_fp16_serial1
done
if [[ ${GRID:-full} == full ]]; then
  for B in 8 16 32 64; do for WT in 500 1000 2000; do run 2 $B $WT W2_B${B}_w${WT} $BK; done; done
  for MX in 3 headline; do for B in 8 64; do for WT in 500 2000; do run $MX $B $WT W${MX}_B${B}_w${WT} $BK; done; done; done
else  # lean grid (86M: no config reaches p99 <= 10 ms at W >= 2; the curves are still recorded)
  for B in 8 16 64; do for WT in 500 2000; do run 2 $B $WT W2_B${B}_w${WT} $BK; done; done
  for B in 8 64; do run headline $B 500 Wheadline_B${B}_w500 $BK; done
fi
kill $DMON
echo DRIVER_A_MB2_DONE $M
