#!/usr/bin/env bash
# prov_stop.sh NAME RUN   SIGTERM synthprov and wait until it has flushed records + stats.json
source "$(dirname "$0")/lib.sh"
name=$1 run=$2
rssh "$name" "cd ~/rv/runs/$run/prov && p=\$(cat synthprov.pid) && kill -TERM \$p 2>/dev/null; \
  for i in \$(seq 600); do kill -0 \$p 2>/dev/null || break; sleep 0.1; done; tail -1 synthprov.log; ls -la records.jsonl stats.json | awk '{print \$5, \$9}'"
