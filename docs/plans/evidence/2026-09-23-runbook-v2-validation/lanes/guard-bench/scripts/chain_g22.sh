#!/usr/bin/env bash
# G2-2 after Triton B-22M: stop Triton, window-execution-mode test, then two knee confirmations (22M serial1 W=2).
set -uo pipefail
M=Llama-Prompt-Guard-2-22M
while ! grep -q DRIVER_B_DONE ~/gb/driver_b_22M.log; do sleep 15; done
sudo docker rm -f triton >/dev/null 2>&1
source ~/gb/gpuenv.sh; cd ~/gb; mkdir -p ~/gb/out/A/$M
python scripts/parallel_windows.py --onnx-dir $HOME/gb/onnx/$M --tok-dir $HOME/gb/models/$M --corpus $HOME/gb/corpus \
  --Ws 2,3 --modes batch,serial,streams --out ~/gb/out/A/$M/parallel_windows_g22.json > ~/gb/out/A/$M/parallel_windows_g22.log 2>&1
bash ~/gb/scripts/driver_knee.sh $M trt_fp16_serial1 2 1 0 81 122 10 serial1_W2_p99le10
bash ~/gb/scripts/driver_knee.sh $M trt_fp16_serial1 2 1 0 41 81 8 serial1_W2_p99le8
echo CHAIN_G22_DONE
