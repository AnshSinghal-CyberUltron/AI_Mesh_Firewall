#!/usr/bin/env bash
# Installs docker (+ nvidia runtime when a GPU is present) and pulls the Triton images.
set -euxo pipefail
ROLE=$1   # server|client
while sudo fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; do sleep 3; done
sudo DEBIAN_FRONTEND=noninteractive apt-get update -q
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q docker.io netperf
if [[ $ROLE == server ]]; then
  sudo nvidia-ctk runtime configure --runtime=docker
  sudo systemctl restart docker
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q sockperf || true
  time sudo docker pull nvcr.io/nvidia/tritonserver:26.05-py3
fi
true
sudo docker images
echo DOCKER_DONE
