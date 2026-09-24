#!/usr/bin/env bash
# G2-4 after Triton B-86M: repo bake-off script verbatim (third-party int8 ONNX), then multi-process contention (22M).
set -uo pipefail
while ! grep -q DRIVER_B_DONE ~/gb/driver_b_86M.log; do sleep 15; done
sudo docker rm -f triton >/dev/null 2>&1
source ~/gb/gpuenv.sh; cd ~/gb
python repo_scripts/pg2_input_latency.py --model-dir $HOME/gb/bakeoff_model --out ~/gb/out/A/bakeoff/pg2_input_latency.json \
  > ~/gb/out/A/bakeoff/pg2_input_latency.log 2>&1
bash ~/gb/scripts/driver_mp.sh Llama-Prompt-Guard-2-22M trt_fp16_serial1 2 1 0 50,100,150,200,250,300,350,400,450,500
echo CHAIN_G24_DONE
