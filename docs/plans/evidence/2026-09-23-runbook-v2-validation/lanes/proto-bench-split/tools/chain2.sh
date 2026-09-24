#!/usr/bin/env bash
# chain2.sh: after the 1-guard repeats/Poisson/DIRECT: (a) Poisson ladder DOWN from 153 until the first PASS
# (the Poisson run at the constant-arrival knee, s1p-191, failed on guard-deadline expiries);
# (b) reconfigure the gateway to TWO guard owners; (c) 2-guard constant ladder from 238 up to the first FAIL;
# (d) 2 repeats at the 2-guard knee + one Poisson run there.
set -uo pipefail
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
E=$SP/evidence/proto-bench-split; T=$E/tools
RUNS=/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs; LOGS=$(dirname "$RUNS")/logs
verdict() { python3 -c "import json; d=json.load(open('$RUNS/$1/split_metrics.json')); L=d['latency_ms']; print(d['verdict'], 'q/s', d['qualified_rps'], 'infra%', d['infra_pct'], 'drops', d['drops'], 'p99 json', L['JSON_total'].get('p99'), 'sse-total', L['SSE_total'].get('p99'), 'sse-first', L['SSE_first_tok1'].get('p99'))" 2>/dev/null || echo NO_VERDICT; }
step() { local name=$1 rate=$2; shift 2
  echo "$(date -u +%T) START $name rate=$rate env=[$*]"
  env "$@" bash "$T/split_step.sh" "$name" "$rate" headline-22M.jsonl > "$LOGS/$name.log" 2>&1
  echo "$(date -u +%T) END $name $(verdict "$name")"; }
for r in 153 122 98; do
  step "s1p-$(printf %03d $r)" "$r" LABEL=poisson_down_1guard OLG_EXTRA="-sample-mod 1 -arrival poisson"
  [[ $(verdict "s1p-$(printf %03d $r)") == PASS* ]] && break
done
echo "$(date -u +%T) RECONFIGURE gateway -> 2 guard owners"
bash "$T/gw_restart.sh" gw-b-2guards "10.160.15.228:7070,10.160.15.232:7070"
export GUARDS="rv-split-guard-1 rv-split-guard-2"
bash "$T/ladder.sh" s2 "238 298 373 466 582 728" LABEL=ladder_2guards GUARDS="$GUARDS" > "$E/notes/ladder-s2.log" 2>&1
cat "$E/notes/ladder-s2.log"
knee=$(grep -E "END s2-[0-9]+ PASS" "$E/notes/ladder-s2.log" | tail -1 | sed -E "s/.*END s2-0*([0-9]+) PASS.*/\1/")
if [[ -n "$knee" ]]; then
  k3=$(printf %03d "$knee")
  step "s2-$k3-r2" "$knee" LABEL=knee_repeat_2guards GUARDS="$GUARDS"
  step "s2-$k3-r3" "$knee" LABEL=knee_repeat_2guards GUARDS="$GUARDS"
  step "s2p-$k3" "$knee" LABEL=knee_poisson_2guards GUARDS="$GUARDS" OLG_EXTRA="-sample-mod 1 -arrival poisson"
fi
echo "$(date -u +%T) CHAIN2 DONE"
