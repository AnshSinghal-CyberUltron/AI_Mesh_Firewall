#!/bin/bash
# On the SUT VM. sut.sh start VARIANT WORKERS CPULIST | sut.sh stop
set -u
case $1 in
start)
  pkill -f "[u]vicorn mw_app" ; sleep 1
  cd ~
  MW_VARIANT=$2 setsid nohup taskset -c $4 ~/venv/bin/uvicorn mw_app:app --host 0.0.0.0 --port 8300 \
     --loop uvloop --http httptools --workers $3 --no-access-log --log-level warning \
     --timeout-keep-alive 65 --backlog 4096 > ~/uvicorn_$2_$3.log 2>&1 < /dev/null &
  for i in $(seq 1 50); do curl -s -o /dev/null -w '%{http_code}' -X POST -H 'content-type: application/json' \
     -d '{"model":"x","messages":[{"role":"user","content":"hi"}]}' http://127.0.0.1:8300/v1/chat/completions 2>/dev/null | grep -q 200 && break; sleep 0.2; done
  echo "started variant=$2 workers=$3 cpus=$4 pids=$(pgrep -f '[u]vicorn mw_app' | tr '\n' ',')";;
stop) pkill -f "[u]vicorn mw_app"; sleep 1; echo stopped;;
esac
