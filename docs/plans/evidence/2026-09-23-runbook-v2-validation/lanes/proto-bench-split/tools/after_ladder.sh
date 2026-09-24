#!/usr/bin/env bash
# after_ladder.sh LADDER_LOG PREFIX   (runs when the ladder has stopped)
#   knee = last PASS rate, fail = the rate the ladder stopped at. Then: 2 more repeats at the knee
#   (-r2, -r3; the ladder step is r1), one Poisson run at the knee, one DIRECT floor at the failing rate.
set -uo pipefail
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
T=$SP/evidence/proto-bench-split/tools
RUNS=/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs
LOGS=$(dirname "$RUNS")/logs
log=$1 prefix=$2
knee=$(grep -E "END $prefix-[0-9]+ PASS" "$log" | tail -1 | sed -E "s/.*END $prefix-0*([0-9]+) PASS.*/\1/")
fail=$(grep -E "ladder stops at $prefix-" "$log" | tail -1 | sed -E "s/.*stops at $prefix-0*([0-9]+).*/\1/")
echo "$(date -u +%T) knee=$knee fail=$fail"
verdict() { python3 -c "import json; d=json.load(open('$RUNS/$1/split_metrics.json')); L=d['latency_ms']; print(d['verdict'], 'q/s', d['qualified_rps'], 'infra%', d['infra_pct'], 'drops', d['drops'], 'p99 json', L['JSON_total'].get('p99'), 'sse-total', L['SSE_total'].get('p99'), 'sse-first', L['SSE_first_tok1'].get('p99'))" 2>/dev/null || echo NO_VERDICT; }
step() {  # name rate [env...]
  local name=$1 rate=$2; shift 2
  echo "$(date -u +%T) START $name rate=$rate env=[$*]"
  env "$@" bash "$T/split_step.sh" "$name" "$rate" headline-22M.jsonl > "$LOGS/$name.log" 2>&1
  echo "$(date -u +%T) END $name $(verdict "$name")"
}
k3=$(printf %03d "$knee"); f3=$(printf %03d "$fail")
step "$prefix-$k3-r2" "$knee" LABEL=knee_repeat
step "$prefix-$k3-r3" "$knee" LABEL=knee_repeat
step "${prefix}p-$k3" "$knee" LABEL=knee_poisson OLG_EXTRA="-sample-mod 1 -arrival poisson"
step "d${prefix#s}-$f3" "$fail" LABEL=direct_floor_at_first_fail MODE=direct
echo "$(date -u +%T) AFTER-LADDER DONE"
