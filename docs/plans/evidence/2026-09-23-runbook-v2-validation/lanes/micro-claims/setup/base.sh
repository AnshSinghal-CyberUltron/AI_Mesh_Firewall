#!/bin/bash
# Base bootstrap for every rv-micro VM. Arg1 = role (py|redis|edge|lg)
set -euo pipefail
ROLE=$1
export DEBIAN_FRONTEND=noninteractive
sudo -E apt-get update -qq >/dev/null
PKGS="sysstat jq git"
case $ROLE in
  py)    PKGS="$PKGS redis-tools";;
  redis) PKGS="$PKGS docker.io redis-tools";;
  edge)  PKGS="$PKGS docker.io";;
  lg)    PKGS="$PKGS build-essential libssl-dev zlib1g-dev unzip";;
esac
sudo -E apt-get install -y -qq $PKGS >/dev/null
if [ "$ROLE" != "redis" ] && [ "$ROLE" != "edge" ]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
  ~/.local/bin/uv python install 3.12 >/dev/null 2>&1
  ~/.local/bin/uv venv -q --python 3.12 ~/venv
fi
if [ "$ROLE" = "py" ]; then
  # exact v1 pins from gateway/uv.lock (starlette/fastapi/uvicorn/uvloop/httptools/anyio/h11/redis/gunicorn); no hiredis (not in lock)
  VIRTUAL_ENV=~/venv ~/.local/bin/uv pip install -q starlette==1.2.0 fastapi==0.136.3 uvicorn==0.48.0 uvloop==0.22.1 \
     httptools==0.8.0 anyio==4.13.0 h11==0.16.0 redis==8.0.0 gunicorn==26.0.0 orjson==3.11.9 httpx
fi
if [ "$ROLE" = "lg" ]; then
  VIRTUAL_ENV=~/venv ~/.local/bin/uv pip install -q httpx==0.28.1 h11==0.16.0
  # wrk2 fallback (constant-throughput, CO-corrected)
  if [ ! -x ~/wrk2/wrk ]; then git clone -q https://github.com/giltene/wrk2.git ~/wrk2 && make -C ~/wrk2 -j8 >/dev/null 2>&1 || true; fi
  # client-side only: allow many outbound conns
  sudo sysctl -q -w net.ipv4.ip_local_port_range="1024 65000" net.ipv4.tcp_tw_reuse=1 net.core.somaxconn=65535
fi
if [ "$ROLE" = "redis" ] || [ "$ROLE" = "edge" ]; then sudo usermod -aG docker rv || true; fi
echo "SETUP_DONE role=$ROLE"
