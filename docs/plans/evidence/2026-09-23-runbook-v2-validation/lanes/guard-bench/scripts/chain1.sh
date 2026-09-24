#!/usr/bin/env bash
# After the latency driver: E scoring (+tokenizer timing), then the bucketed micro-batch grid. usage: chain1.sh <model> <waitlog> [tok]
set -uo pipefail
M=$1; WAITLOG=$2; TOKB=${3:-}
while ! grep -q DRIVER_A_LATENCY_DONE $WAITLOG; do sleep 10; done
bash ~/gb/scripts/driver_e_tok.sh $M $TOKB
bash ~/gb/scripts/driver_a_mb2.sh $M trt_fp16_bucketed 16 "2 3 headline" "8,16,32,64" "500,1000,2000"
echo CHAIN1_DONE $M
