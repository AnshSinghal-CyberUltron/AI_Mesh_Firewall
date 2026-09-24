#!/usr/bin/env bash
# Instrument honesty (PROTO_SPEC E2E): the harness must SEE an injected 5 ms pre-dispatch delay
# and a 30 ms hold after upstream chunk 50. Functional-scale (4 RPS) runs on one host:
# synthprov (TTFT 150 ms, ITL 20 ms, all requests chunk-sampled), rvproto local_cpu, olg.
set -euo pipefail
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
H=$SP/harness; RVP=$SP/rvproto; OUT=${HONESTY_OUT:-$SP/evidence/proto-builder/honesty}; mkdir -p "$OUT"
[ -f "$OUT/light-corpus.jsonl" ] || cp "$SP/evidence/proto-builder/honesty/light-corpus.jsonl" "$OUT/"
RATE=${RATE:-4}; DUR=${DUR:-60}
CORPUS=$OUT/light-corpus.jsonl
run() {  # $1=name $2=target $3=extra rvproto env assignments (or "direct")
  local name=$1 target=$2; shift 2
  local d=$OUT/$name; rm -rf "$d"; mkdir -p "$d"
  bash "$RVP/tools/local_down.sh"
  if [[ $target == direct ]]; then
    setsid nohup "$H/bin/synthprov" -listen 127.0.0.1:18080 -record "$d/provider.jsonl" -sample-mod 1 \
      -stats-out "$d/synthprov-stats.json" > "$d/synthprov.log" 2>&1 &
    echo $! > "$RVP/.run/provider.pid"; sleep 1; url=http://127.0.0.1:18080
  else
    PROVIDER=synthprov SYNTHPROV_BIN=$H/bin/synthprov \
      SYNTHPROV_ARGS="-listen 127.0.0.1:18080 -record $d/provider.jsonl -sample-mod 1 -stats-out $d/synthprov-stats.json" \
      RV_METRICS_DIR=$d/metrics bash "$RVP/tools/local_up.sh" "$d" "$@" > "$d/up.log"
    url=http://127.0.0.1:8400
  fi
  "$H/bin/olg" -targets "$url" -rate "$RATE" -duration "$DUR" -warmup 5 -corpus "$CORPUS" -sse-frac 0.7 \
    -sample-mod 1 -auth "Bearer sk-rv-org-a-0001" -out "$d/olg" -label "honesty-$name" -seed 7 > "$d/olg.log" 2>&1
  sleep 2
  [[ $target == direct ]] || curl -s localhost:8400/metrics/all > "$d/rvproto-metrics.json"
  bash "$RVP/tools/local_down.sh"
  local mode=sut; [[ $target == direct ]] && mode=direct
  python3 "$H/analyze.py" --client "$d/olg/requests.jsonl" --provider "$d/provider.jsonl" --mode $mode \
    --policy enforce --corpus "$CORPUS" --profile-stages canon,det,sem,resolve,dispatch,out,audit \
    --out "$d/analysis" > "$d/analyze.log" 2>&1 || true
}
run direct direct
run base rvproto
run predispatch5 rvproto RV_INJECT_PREDISPATCH_MS=5
run hold30 rvproto RV_INJECT_HOLD=50:30
echo HONESTY_DONE
