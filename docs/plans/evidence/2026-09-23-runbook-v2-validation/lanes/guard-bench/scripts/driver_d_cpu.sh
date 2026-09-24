#!/usr/bin/env bash
# Exp D: CPU-only ORT latency + throughput for one model. usage: driver_d_cpu.sh <model> <threads-list> <backends>
set -uo pipefail
M=$1; TH=$2; BK=$3
cd ~/gb; O=~/gb/out/D/$M; mkdir -p $O
F="--onnx-dir $HOME/gb/onnx/$M --tok-dir $HOME/gb/models/$M --corpus $HOME/gb/corpus"
~/venv/bin/python scripts/cpu_bench.py latency $F --backends $BK --threads $TH --ws 1,2,3 --out $O/cpu_latency.json > $O/cpu_latency.log 2>&1
~/venv/bin/python scripts/cpu_bench.py throughput $F --backends $BK --threads $TH --ws 1 --secs 30 --out $O/cpu_throughput.json > $O/cpu_throughput.log 2>&1
echo DRIVER_D_DONE $M
