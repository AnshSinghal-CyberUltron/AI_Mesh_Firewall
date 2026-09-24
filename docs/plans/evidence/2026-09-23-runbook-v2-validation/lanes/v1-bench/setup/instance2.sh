#!/usr/bin/env bash
# instance2.sh — runs on the second v1 instance (rv-v1-*-2), full profile, same build 5af9594a2631.
set -uo pipefail
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
E=$SP/evidence/v1-bench
PY=/home/contact_cyberultron_com/AI_Mesh_Firewall/gateway/.venv/bin/python
step() {  # step RUN RATE RAMP WARM DUR [PROV_FLAGS] [EXTRA olg flags]
  local run=$1 rate=$2 ramp=$3 warm=$4 dur=$5 pf=${6:--ttft 150ms -itl 20ms}; shift 6 || shift $#
  echo "$(date -u +%T) START $run rate=$rate prov='$pf' extra='$*'"
  RAMP_S=$ramp PROV_FLAGS="$pf" bash $E/setup/run_step.sh sut "$run" "$rate" "$warm" "$dur" headline-22M.jsonl "$@" > "$E/runs/$run.log" 2>&1
  echo "$(date -u +%T) DONE $run"
  $PY $E/setup/ladder_table.py "$run" 2>&1 | tail -1
}
step warmup-i2 20 10 0 60
step N-r10 10 30 60 300
step N-r1 1 30 60 300
step N-r25 25 30 60 300
step ITL10-sse-r10 10 10 30 180 "-ttft 150ms -itl 10ms" -sse-frac 1.0
step ITL20-sse-r10 10 10 30 180 "-ttft 150ms -itl 20ms" -sse-frac 1.0
step ITL30-sse-r10 10 10 30 180 "-ttft 150ms -itl 30ms" -sse-frac 1.0
step N-r50 50 30 60 300
step OVL-r100 100 10 0 120
echo "$(date -u +%T) ALL DONE"
