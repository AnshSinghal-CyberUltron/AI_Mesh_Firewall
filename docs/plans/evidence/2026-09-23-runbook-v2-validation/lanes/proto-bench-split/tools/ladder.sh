#!/usr/bin/env bash
# ladder.sh PREFIX "RATE RATE ..." [ENV=VAL ...]
#   Runs split_step.sh at each rate in order and stops after the first step whose verdict.json
#   (C4 p99 < 20 ms, infra <= 0.1%, 0 drops) is FAIL. Prints one line per step.
set -uo pipefail
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
T=$SP/evidence/proto-bench-split/tools
RUNS=${RUNS:-/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs}
LOGS=$(dirname "$RUNS")/logs; mkdir -p "$LOGS"
prefix=$1 rates=$2; shift 2
for r in $rates; do
  run=$prefix-$(printf %03d "$r")
  echo "$(date -u +%T) START $run rate=$r env=[$*]"
  env "$@" bash "$T/split_step.sh" "$run" "$r" headline-22M.jsonl > "$LOGS/$run.log" 2>&1
  v=$(python3 -c "import json; d=json.load(open('$RUNS/$run/verdict.json')); print(d['verdict'], 'q/s', d['qualified_rps'], 'C4 p99', d['c4_p99'], 'infra%', d['infra_pct'], 'drops', d['drops'], 'json', d['json_total_p99'], 'sse-total', d['sse_total_p99'], 'sse-first', d['sse_first_p99'])" 2>/dev/null || echo "NO_VERDICT")
  echo "$(date -u +%T) END $run $v"
  [[ $v == PASS* ]] || { echo "$(date -u +%T) ladder stops at $run"; break; }
done
