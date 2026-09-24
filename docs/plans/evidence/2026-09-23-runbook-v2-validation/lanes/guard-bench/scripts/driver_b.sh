#!/usr/bin/env bash
# Exp B: node-local Triton, loopback gRPC client. A FRESH tritonserver is started for every batcher config
# (in-place reloads with a new config segfaulted Triton 26.05, signal 11), then the config is pushed and measured.
# usage: driver_b.sh <model> <triton-name> <tag> <cap_wps> <Ws> <"MB:Q[:I] ..." batcher configs> [skip_latency]
set -uo pipefail
M=$1; TN=$2; TAG=$3; CAP=$4; WS=$5; CFGS=$6; SKIPLAT=${7:-0}
cd ~/gb; PY=~/venv/bin/python
O=~/gb/out/B/$M/triton_$TAG; mkdir -p $O
F="--url localhost:8001 --model $TN --tok-dir $HOME/gb/models/$M --corpus $HOME/gb/corpus"
RATES=$($PY -c "c=$CAP; print(','.join(str(round(c*f,1)) for f in (0.05,0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9,1.0,1.1)))")
up() { bash ~/gb/scripts/triton_up.sh $TAG > /dev/null; }
if [[ $SKIPLAT == 0 ]]; then
  up
  $PY scripts/triton_bench.py load $F --queue-us -1 --max-batch 64 --instances 1 > $O/load_nobatch.json 2>&1
  $PY scripts/triton_bench.py latency $F --ws 1,2,3,4,7 --iters 3000 --warmup 300 --out $O/lat_rtt.json > $O/lat_rtt.log 2>&1
fi
for W in ${WS//,/ }; do
  up
  $PY scripts/triton_bench.py ladder $F --W $W --queue-us -1 --max-batch 64 --instances 1 --rates $RATES --dur 10 --warm 2 \
     --stop-p99-ms 60 --out-dir $O/W${W}_nobatch > $O/W${W}_nobatch.log 2>&1
  for C in $CFGS; do
    MB=${C%%:*}; REST=${C#*:}; Q=${REST%%:*}; I=1; [[ $REST == *:* ]] && I=${REST#*:}
    up
    $PY scripts/triton_bench.py ladder $F --W $W --queue-us $Q --max-batch $MB --instances $I --rates $RATES --dur 10 --warm 2 \
       --stop-p99-ms 60 --out-dir $O/W${W}_mb${MB}_q${Q}_i${I} > $O/W${W}_mb${MB}_q${Q}_i${I}.log 2>&1
  done
done
sudo docker logs triton > $O/triton_last.log 2>&1
echo DRIVER_B_DONE $M $TAG
