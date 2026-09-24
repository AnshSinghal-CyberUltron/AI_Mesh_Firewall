#!/usr/bin/env bash
# Exp A multi-process contention: N in {4,8} separate processes, each with its own TensorRT session, +/- CUDA MPS.
# usage: driver_mp.sh <model> <backend> <W> <B> <wait_us> <"rates_total list"> [Ns]
set -uo pipefail
M=$1; BK=$2; W=$3; B=$4; WT=$5; RATES=$6; NS=${7:-"4 8"}
source ~/gb/gpuenv.sh; cd ~/gb
O=~/gb/out/A/$M/mp_$BK; mkdir -p $O
F="--onnx-dir $HOME/gb/onnx/$M --tok-dir $HOME/gb/models/$M --corpus $HOME/gb/corpus --backend $BK"
nvidia-smi dmon -s pucvmet -d 1 -o T > $O/dmon_$(date +%s).log 2>&1 &
DMON=$!
for N in $NS; do
  for MPS in 0 1; do
    python scripts/multiproc.py closed $F --N $N --mps $MPS --W $W --secs 20 --out-dir $O/closed_N${N}_mps${MPS} > $O/closed_N${N}_mps${MPS}.log 2>&1
    python scripts/multiproc.py open $F --N $N --mps $MPS --W $W --B $B --wait-us $WT --rates-total $RATES --dur 10 --warm 2 \
       --out-dir $O/open_N${N}_mps${MPS}_B${B}_w${WT} > $O/open_N${N}_mps${MPS}_B${B}_w${WT}.log 2>&1
  done
done
kill $DMON
echo DRIVER_MP_DONE $M
