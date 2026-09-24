#!/usr/bin/env bash
# Stop only what up.sh started (process groups recorded in our own run dir). Keeps the redis container
# unless KILL_REDIS=1.
EV=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/reviewer-state-propagation
RUN=$EV/run
for f in "$RUN"/*.pid; do
  [[ -f "$f" ]] || continue
  pid=$(cat "$f")
  kill -TERM -- "-$pid" 2>/dev/null || true
  for i in $(seq 1 50); do kill -0 -- "-$pid" 2>/dev/null || break; sleep 0.2; done
  kill -KILL -- "-$pid" 2>/dev/null || true
  kill -KILL "$pid" 2>/dev/null || true
  rm -f "$f"
done
if [[ "${KILL_REDIS:-0}" == "1" ]]; then docker rm -f sp-rsp-redis >/dev/null 2>&1 || true; fi
exit 0
