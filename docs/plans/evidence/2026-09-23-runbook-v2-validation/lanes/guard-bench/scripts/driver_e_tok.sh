#!/usr/bin/env bash
# Exp E GPU scoring (+ optional tokenizer timing) for one model. usage: driver_e_tok.sh <model> [tok]
set -uo pipefail
M=$1; TOKB=${2:-}
source ~/gb/gpuenv.sh; cd ~/gb
O=~/gb/out/E/$M; mkdir -p $O
python scripts/gpu_scores.py --onnx-dir $HOME/gb/onnx/$M --tok-dir $HOME/gb/models/$M --corpus $HOME/gb/corpus \
  --backends trt_fp16_dyn,trt_fp16_static,cuda_fp32,cuda_fp16 --n 1000 --out-dir $O > $O/gpu_scores.log 2>&1
cp $HOME/gb/onnx/$M/ref_scores.json $O/
if [[ $TOKB == tok ]]; then
  mkdir -p ~/gb/out/A/tok
  python scripts/tok_bench.py $HOME/gb/models/Llama-Prompt-Guard-2-22M,$HOME/gb/models/Llama-Prompt-Guard-2-86M $HOME/gb/corpus ~/gb/out/A/tok/tok_bench.json > ~/gb/out/A/tok/tok_bench.log 2>&1
fi
echo DRIVER_E_DONE $M
