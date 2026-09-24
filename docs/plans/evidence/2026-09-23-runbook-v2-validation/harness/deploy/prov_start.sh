#!/usr/bin/env bash
# prov_start.sh NAME RUN [synthprov flags...]   start synthprov (fresh record file) in ~/rv/runs/RUN/prov
#   default listen :8080-:8083 (several ports spread ephemeral-port use); override with -listen.
source "$(dirname "$0")/lib.sh"
name=$1 run=$2; shift 2
flags="$*"
[[ "$flags" == *-listen* ]] || flags="-listen :8080,:8081,:8082,:8083 $flags"
rssh "$name" "set -e; d=~/rv/runs/$run/prov; rm -rf \$d; mkdir -p \$d; cd \$d; cp ~/rv/hostinfo.txt .; \
  nohup env ${PROV_ENV:-} ~/rv/bin/synthprov -record records.jsonl -stats-out stats.json -cpu-log cpu.jsonl $flags > synthprov.log 2>&1 < /dev/null & \
  echo \$! > synthprov.pid; for i in \$(seq 100); do grep -q 'listening on' synthprov.log && break; sleep 0.1; done; \
  sleep 0.3; kill -0 \$(cat synthprov.pid) && grep 'listening on' synthprov.log | head -1"
