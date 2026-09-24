#!/usr/bin/env bash
# ladder.sh PREFIX RATE [RATE...]  — HEADLINE open-loop ladder on v1 (ramp 30 + warm-up 60 + 300 s measured
# per step, harness defaults), one run_step.sh per rate, sequential; prints the per-step table row.
set -uo pipefail
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
E=$SP/evidence/v1-bench
PY=/home/contact_cyberultron_com/AI_Mesh_Firewall/gateway/.venv/bin/python
prefix=$1; shift
for r in "$@"; do
  run="${prefix}-r${r}"
  echo "$(date -u +%T) START $run rate=$r"
  RAMP_S=${RAMP_S:-30} bash $E/setup/run_step.sh sut "$run" "$r" ${WARMUP_S:-60} ${DURATION_S:-300} ${CORPUS:-headline-22M.jsonl} ${EXTRA:-} > "$E/runs/$run.log" 2>&1
  echo "$(date -u +%T) DONE $run rc=$?"
  $PY $E/setup/ladder_table.py "$run" 2>&1 | tail -1
done
