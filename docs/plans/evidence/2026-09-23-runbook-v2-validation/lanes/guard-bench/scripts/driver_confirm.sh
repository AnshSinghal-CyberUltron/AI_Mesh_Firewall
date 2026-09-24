#!/usr/bin/env bash
# Knee confirmation (GCP.md hygiene): >= 5 min steady state per step, 3 repeats at the knee + the next (failing) step.
# usage: driver_confirm.sh <model> <backend> <mix> <B> <wait_us> <knee_wps> <next_wps> <tag>
set -uo pipefail
M=$1; BK=$2; MX=$3; B=$4; WT=$5; KNEE=$6; NEXT=$7; TAG=$8
source ~/gb/gpuenv.sh; cd ~/gb
O=~/gb/out/A/$M/confirm_$TAG; mkdir -p $O
F="--onnx-dir $HOME/gb/onnx/$M --tok-dir $HOME/gb/models/$M --corpus $HOME/gb/corpus"
if [[ $MX == headline ]]; then WARG="--W 2 --wmix headline"; else WARG="--W $MX"; fi
MB=$B; [[ $B -lt 4 ]] && MB=4
nvidia-smi dmon -s pucvmet -d 1 -o T > $O/dmon.log 2>&1 &
DMON=$!
python scripts/microbatch_bench.py $F --backend $BK --max-batch $MB $WARG --B $B --wait-us $WT \
   --rates $KNEE,$KNEE,$KNEE,$NEXT --dur 300 --warm 10 --min-reqs 0 --no-stop --out-dir $O > $O/run.log 2>&1
kill $DMON
echo DRIVER_CONFIRM_DONE $M $TAG
