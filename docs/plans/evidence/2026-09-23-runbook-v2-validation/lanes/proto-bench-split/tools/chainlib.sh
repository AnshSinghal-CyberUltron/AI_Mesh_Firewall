# chainlib.sh (sourced by chainD.sh / chainE.sh): step + ladder helpers of chainB.sh, plus a corpus argument
# (CORPUS=..., default headline-22M.jsonl) and KNEE_RESULT (the knee found by the last ladder_knee call).
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
E=$SP/evidence/proto-bench-split; T=$E/tools
export LANE=split EVID=$E; source "$SP/harness/deploy/lib.sh"; set +e
RUNS=/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs; LOGS=$(dirname "$RUNS")/logs
G1=10.160.0.10:7070 G2=10.160.15.248:7070 G3=10.160.15.247:7070 G4=10.160.15.246:7070 G5=10.160.15.245:7070 G6=10.160.0.61:7070
LIB="rvproto-frozen-1+li-knobs tree 8ace2294589c0e75 knobs ON: gateways RV_METRICS_DUMP_S=0 RV_TOKENIZE_IN_THREAD=1 TOKENIZERS_PARALLELISM=false; owners RV_METRICS_DUMP_S=0"
KNOBS=(RV_METRICS_DUMP_S=0 RV_TOKENIZE_IN_THREAD=1 TOKENIZERS_PARALLELISM=false)
v() { python3 -c "import json; d=json.load(open('$RUNS/$1/verdict.json')); print(d['verdict'], 'q/s', d['qualified_rps'], 'C4', d['c4_p99'], 'infra%', d['infra_pct'], 'drops', d['drops'], 'json', d['json_total_p99'], 'sse-total', d['sse_total_p99'])" 2>/dev/null || echo NO_VERDICT; }
step() { local name=$1 rate=$2; shift 2
  [[ -d "$RUNS/$name" ]] && { echo "$(date -u +%T) SKIP $name (exists): $(v "$name")"; return; }
  echo "$(date -u +%T) START $name rate=$rate corpus=${CORPUS:-headline-22M.jsonl} env=[$*]"
  env "$@" bash "$T/split_step.sh" "$name" "$rate" "${CORPUS:-headline-22M.jsonl}" > "$LOGS/$name.log" 2>&1
  echo "$(date -u +%T) END $name $(v "$name")"; }
passed() { [[ $(v "$1") == PASS* ]]; }
ladder_knee() {
  local p=$1 up=$2 down=$3; shift 3
  local passes=() r
  KNEE_RESULT=""
  for r in $up; do step "$p-$(printf %03d $r)" "$r" "$@"; if passed "$p-$(printf %03d $r)"; then passes+=("$r"); else break; fi; done
  if [[ ${#passes[@]} == 0 ]]; then
    for r in $down; do step "$p-$(printf %03d $r)" "$r" "$@"; if passed "$p-$(printf %03d $r)"; then passes=("$r"); break; fi; done
  fi
  while [[ ${#passes[@]} -gt 0 ]]; do
    local k=${passes[-1]} k3; k3=$(printf %03d "${passes[-1]}")
    step "$p-$k3-r2" "$k" "$@"; step "$p-$k3-r3" "$k" "$@"
    if passed "$p-$k3-r2" && passed "$p-$k3-r3"; then echo "$(date -u +%T) KNEE $p = $k (3/3 PASS)"; KNEE_RESULT=$k; return; fi
    unset 'passes[-1]'
    if [[ ${#passes[@]} == 0 ]]; then
      for r in $down; do [[ $r -lt $k ]] || continue; step "$p-$(printf %03d $r)" "$r" "$@"; passed "$p-$(printf %03d $r)" && { passes=("$r"); break; }; done
    fi
  done
  echo "$(date -u +%T) KNEE $p = none"
}
