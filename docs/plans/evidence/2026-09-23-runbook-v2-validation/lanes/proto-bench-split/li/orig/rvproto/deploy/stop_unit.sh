#!/usr/bin/env bash
# Stop rvproto (whole process group) and the Triton container if running.
RV=$HOME/rv
if [[ -f $RV/run/rvproto.pid ]]; then
  pid=$(cat "$RV/run/rvproto.pid")
  kill -TERM -- "-$pid" 2>/dev/null || true
  for i in $(seq 1 50); do kill -0 -- "-$pid" 2>/dev/null || break; sleep 0.2; done
  kill -KILL -- "-$pid" 2>/dev/null || true
  rm -f "$RV/run/rvproto.pid"
fi
sudo docker rm -f rv-triton >/dev/null 2>&1 || true
