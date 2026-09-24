#!/usr/bin/env bash
# proxy_stop.sh NAME RUN
source "$(dirname "$0")/lib.sh"
name=$1 run=$2
rssh "$name" "cd ~/rv/runs/$run/proxy && p=\$(cat rvproxy.pid) && kill -TERM \$p 2>/dev/null; \
  for i in \$(seq 300); do kill -0 \$p 2>/dev/null || break; sleep 0.1; done; tail -1 rvproxy.log"
