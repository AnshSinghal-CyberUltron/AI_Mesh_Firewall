#!/bin/bash
# On rv-micro-up-1. up.sh app | synth | stop     (upstream on :8300, like gateway:8300)
pkill -f "[g]unicorn mw_app"; pkill -f "[s]ynthprov -listen"; sleep 1
case $1 in
app) cd ~
     MW_VARIANT=none setsid nohup ~/venv/bin/gunicorn mw_app:app -k uvicorn.workers.UvicornWorker \
       --bind 0.0.0.0:8300 --workers 8 --worker-connections 20000 --timeout 120 --graceful-timeout 60 --keep-alive 65 \
       > ~/gunicorn.log 2>&1 < /dev/null &
     sleep 4; echo "app started: $(pgrep -c -f '[g]unicorn mw_app') gunicorn procs";;
synth) cd ~
     setsid nohup ~/synthprov -listen :8300 -ttft 150ms -itl 20ms -sample-mod 1 -record ~/synth_records_$2.jsonl \
       > ~/synth_$2.log 2>&1 < /dev/null &
     sleep 1; echo "synthprov started record=synth_records_$2.jsonl";;
stop) echo stopped;;
esac
