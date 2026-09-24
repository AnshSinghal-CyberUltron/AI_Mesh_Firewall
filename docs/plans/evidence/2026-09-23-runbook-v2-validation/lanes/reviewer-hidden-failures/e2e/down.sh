#!/usr/bin/env bash
RUN=$(dirname "$0")/.run
for f in "$RUN"/*.pid; do [[ -f "$f" ]] || continue; pid=$(cat "$f"); kill -TERM -- "-$pid" 2>/dev/null || true
  for i in $(seq 1 50); do kill -0 -- "-$pid" 2>/dev/null || break; sleep 0.2; done; kill -KILL -- "-$pid" 2>/dev/null || true; rm -f "$f"; done
