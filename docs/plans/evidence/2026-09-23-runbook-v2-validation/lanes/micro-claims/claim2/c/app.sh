#!/bin/bash
# On rv-micro-app-1. app.sh start EMITS MODE GW_LEVEL | stop     (1 uvicorn worker pinned to vCPU 2, uvloop+httptools)
pkill -f "[u]vicorn loglag_app"; sleep 1
case $1 in
start)
  cd ~
  EMITS=$2 LOGMODE=$3 GW_LEVEL=$4 REDIS_URL=redis://10.160.0.33:6379/0 setsid nohup taskset -c 2 ~/venv/bin/uvicorn loglag_app:app \
     --host 0.0.0.0 --port 8300 --loop uvloop --http httptools --workers 1 --no-access-log --log-level warning \
     --timeout-keep-alive 65 > ~/loglag_$2_$3.log 2>&1 < /dev/null &
  for i in $(seq 1 50); do curl -s -o /dev/null http://127.0.0.1:8300/_lag 2>/dev/null && break; sleep 0.2; done
  echo "started EMITS=$2 MODE=$3 LEVEL=$4 pid=$(pgrep -f '[u]vicorn loglag_app')";;
stop) echo stopped;;
esac
