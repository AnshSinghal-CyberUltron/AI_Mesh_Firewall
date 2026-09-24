#!/usr/bin/env bash
# Start tritonserver (explicit model control; configs are pushed per run by triton_bench.py) and wait until live.
# usage: triton_up.sh <tag e.g. 26.05>
set -uo pipefail
TAG=$1
sudo docker rm -f triton >/dev/null 2>&1
sudo docker run -d --name triton --gpus all --net host --shm-size 2g --ulimit memlock=-1 \
  -v $HOME/gb/triton/models_$TAG:/models nvcr.io/nvidia/tritonserver:$TAG-py3 \
  tritonserver --model-repository=/models --model-control-mode=explicit --disable-auto-complete-config --log-verbose=0 > /dev/null
for i in $(seq 1 120); do curl -sf localhost:8000/v2/health/live >/dev/null && break; sleep 1; done
curl -s localhost:8000/v2 ; echo
sudo docker logs triton 2>&1 | head -30 > $HOME/gb/triton_$TAG.startlog
echo TRITON_UP
