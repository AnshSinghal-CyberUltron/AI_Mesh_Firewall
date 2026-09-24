#!/usr/bin/env bash
# proxy_start.sh NAME RUN UPSTREAM_URL [rvproxy flags...]   start rvproxy on :9000 in ~/rv/runs/RUN/proxy
source "$(dirname "$0")/lib.sh"
name=$1 run=$2 up=$3; shift 3
rssh "$name" "set -e; d=~/rv/runs/$run/proxy; rm -rf \$d; mkdir -p \$d; cd \$d; cp ~/rv/hostinfo.txt .; \
  nohup ~/rv/bin/rvproxy -listen :9000 -upstream $up -log proxy.jsonl $* > rvproxy.log 2>&1 < /dev/null & echo \$! > rvproxy.pid; \
  sleep 0.5; kill -0 \$(cat rvproxy.pid) && cat rvproxy.log"
