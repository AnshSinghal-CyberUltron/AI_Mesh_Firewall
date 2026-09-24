#!/usr/bin/env bash
# restart4.sh [ENV=VAL ...]  -- restart the 4 scaling units (shared Redis rv-pbf-redis-1) in parallel with extra env
S=$(cd "$(dirname "$0")" && pwd)
for pair in "10.146.0.2 http://10.146.0.8:8080" "10.146.0.5 http://10.146.0.6:8080" "10.146.0.3 http://10.146.0.8:8081" "10.146.0.4 http://10.146.0.6:8081"; do
  bash $S/unit_ctl.sh start $pair "$@" &
done
wait
