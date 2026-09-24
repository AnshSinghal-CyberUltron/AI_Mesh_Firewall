#!/usr/bin/env bash
# Reproducible rvproto serving-unit setup. Target: GCP g2-standard-* on
#   --image-project=deeplearning-platform-release --image-family=common-cu129-ubuntu-2204-nvidia-580
# Usage (on the unit):  bash unit_setup.sh ~/rvproto-unit.tar.gz
# Idempotent. Produces: ~/rv/{rvproto,vendor/gateway_v2,models,venv}, redis-server (local, bound to
# localhost, for single-unit runs), docker + nvidia runtime + Triton 26.05 image + a TensorRT plan
# model repository for the triton_grpc backend. Nothing here starts serving; see start_unit.sh.
set -euxo pipefail
TARBALL=${1:-$HOME/rvproto-unit.tar.gz}
RV=$HOME/rv
TRITON_IMAGE=${TRITON_IMAGE:-nvcr.io/nvidia/tritonserver:26.05-py3}
mkdir -p "$RV"
tar -xzf "$TARBALL" -C "$RV"
while sudo fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; do sleep 3; done
sudo DEBIAN_FRONTEND=noninteractive apt-get update -q
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q redis-server docker.io jq
sudo sed -i 's/^save .*/save ""/' /etc/redis/redis.conf || true
sudo systemctl enable --now redis-server
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
[ -x "$HOME/.local/bin/uv" ] || curl -LsSf https://astral.sh/uv/install.sh | sh
[ -x "$RV/venv/bin/python" ] || "$HOME/.local/bin/uv" venv -q -p 3.12 "$RV/venv"
"$HOME/.local/bin/uv" pip install -q -p "$RV/venv/bin/python" -r "$RV/rvproto/deploy/requirements-unit.txt"
sudo docker pull -q "$TRITON_IMAGE"
bash "$RV/rvproto/deploy/triton_build.sh" "$TRITON_IMAGE"
"$RV/venv/bin/python" -c "import onnxruntime as o, tokenizers, hyperscan, tritonclient.grpc.aio; print('ort', o.__version__, o.get_available_providers())"
nvidia-smi -L
echo UNIT_SETUP_DONE
