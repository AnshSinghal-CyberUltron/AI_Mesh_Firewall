#!/usr/bin/env bash
# G2-5 after the off-box runs: in-process W=1 ladders (22M) + knee confirmations for the headline mix (22M, serial1).
set -uo pipefail
M=Llama-Prompt-Guard-2-22M
sudo docker rm -f triton >/dev/null 2>&1
source ~/gb/gpuenv.sh; cd ~/gb
O=~/gb/out/A/$M/mb_trt_fp16_bucketed_g25; mkdir -p $O
F="--onnx-dir $HOME/gb/onnx/$M --tok-dir $HOME/gb/models/$M --corpus $HOME/gb/corpus"
python scripts/microbatch_bench.py $F --backend trt_fp16_serial1 --max-batch 4 --W 1 --B 1 --wait-us 0 \
   --rates 23,47,93,140,186,233,280,326,373,420,466,513 --dur 10 --warm 2 --min-reqs 1000 --max-dur 30 --stop-p99-ms 40 \
   --out-dir $O/W1_serial1 > $O/W1_serial1.log 2>&1
bash ~/gb/scripts/driver_knee.sh $M trt_fp16_serial1 headline 1 0 162 203 10 serial1_headline_p99le10
bash ~/gb/scripts/driver_knee.sh $M trt_fp16_serial1 headline 1 0 81 122 8 serial1_headline_p99le8
echo CHAIN_G25_DONE
