#!/usr/bin/env bash
# chain3.sh: after chain2: 2-guard Poisson ladder DOWN from 298 until the first PASS (the Poisson run at the
# 2-guard constant knee is in chain2), then the DIRECT floor at the 2-guard first failing rate (466).
set -uo pipefail
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
E=$SP/evidence/proto-bench-split; T=$E/tools
RUNS=/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs; LOGS=$(dirname "$RUNS")/logs
verdict() { python3 -c "import json; d=json.load(open('$RUNS/$1/split_metrics.json')); L=d['latency_ms']; print(d['verdict'], 'q/s', d['qualified_rps'], 'infra%', d['infra_pct'], 'drops', d['drops'], 'p99 json', L['JSON_total'].get('p99'), 'sse-total', L['SSE_total'].get('p99'), 'sse-first', L['SSE_first_tok1'].get('p99'))" 2>/dev/null || echo NO_VERDICT; }
step() { local name=$1 rate=$2; shift 2
  echo "$(date -u +%T) START $name rate=$rate env=[$*]"
  env "$@" bash "$T/split_step.sh" "$name" "$rate" headline-22M.jsonl > "$LOGS/$name.log" 2>&1
  echo "$(date -u +%T) END $name $(verdict "$name")"; }
G="rv-split-guard-1 rv-split-guard-2"
if [[ $(verdict s2p-373) != PASS* ]]; then
  for r in 298 238; do
    step "s2p-$(printf %03d $r)" "$r" LABEL=poisson_down_2guards GUARDS="$G" OLG_EXTRA="-sample-mod 1 -arrival poisson"
    [[ $(verdict "s2p-$(printf %03d $r)") == PASS* ]] && break
  done
fi
step d2-466 466 LABEL=direct_floor_at_2guard_first_fail MODE=direct
echo "$(date -u +%T) CHAIN3 DONE"
