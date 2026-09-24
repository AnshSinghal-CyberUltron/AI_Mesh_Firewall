#!/usr/bin/env bash
# Gateway-only node setup (split topology). Target: GCP c4-* on
#   --image-project=ubuntu-os-cloud --image-family=ubuntu-2404-lts-amd64 (hyperdisk-balanced).
# Usage (on the node): bash gateway_setup.sh ~/rvproto-code.tar.gz
# Idempotent. Produces ~/rv/{rvproto,vendor/gateway_v2,models (tokenizers + model sha256 only),venv}
# and a local redis-server bound to localhost. For ONE shared store across several gateway nodes,
# run with REDIS_VPC=1 on the store node: redis then also listens on the internal IP, unauthenticated
# (VPC-internal only, like the guard owners' TCP port; never expose 6379 beyond the VPC).
set -euxo pipefail
TARBALL=${1:-$HOME/rvproto-code.tar.gz}
RV=$HOME/rv
mkdir -p "$RV"
tar -xzf "$TARBALL" -C "$RV"
while sudo fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; do sleep 3; done
sudo DEBIAN_FRONTEND=noninteractive apt-get update -q
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q redis-server jq
sudo sed -i 's/^save .*/save ""/' /etc/redis/redis.conf || true
if [[ "${REDIS_VPC:-0}" == "1" ]]; then
  IP=$(hostname -I | awk '{print $1}')
  sudo sed -i "s/^bind .*/bind 127.0.0.1 $IP/; s/^protected-mode .*/protected-mode no/" /etc/redis/redis.conf
fi
sudo systemctl enable --now redis-server
sudo systemctl restart redis-server
[ -x "$HOME/.local/bin/uv" ] || curl -LsSf https://astral.sh/uv/install.sh | sh
[ -x "$RV/venv/bin/python" ] || "$HOME/.local/bin/uv" venv -q -p 3.12 "$RV/venv"
"$HOME/.local/bin/uv" pip install -q -p "$RV/venv/bin/python" -r "$RV/rvproto/deploy/requirements-gateway.txt"
"$RV/venv/bin/python" -c "import uvloop, httptools, orjson, aiohttp, redis, hiredis, tokenizers, hyperscan, numpy; print('gateway deps ok')"
echo GATEWAY_SETUP_DONE
