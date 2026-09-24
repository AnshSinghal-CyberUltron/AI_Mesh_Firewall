#!/usr/bin/env bash
# reanalyze_all.sh: recompute every step's pbu_summary with the FINAL pbu_analyze.py (pinned harness analyzer
# c9f89fe8) so all summaries come from one analyzer version; arguments are reconstructed from step.json.
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
EV=$SP/evidence/proto-bench-unit
RUNS=/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs
PY=/home/contact_cyberultron_com/AI_Mesh_Firewall/gateway/.venv/bin/python
one() {
  r=$1; d=$RUNS/$r
  [[ -f $d/step.json ]] || return 0
  kind=$(python3 -c "import json;print(json.load(open('$d/step.json')).get('kind','step'))")
  if [[ $kind == overload ]]; then $PY $EV/tools/overload_analyze.py --run $d > $d/overload.stdout 2>&1; cp $d/overload_summary.* $EV/runs/$r/; echo "$r overload"; return 0; fi
  mode=$(python3 -c "import json;print(json.load(open('$d/step.json'))['mode'])")
  corpus=$(python3 -c "import json;print(json.load(open('$d/step.json'))['corpus'])")
  cpath=$SP/harness/corpora/$corpus; [[ -f $EV/corpora/$corpus ]] && cpath=$EV/corpora/$corpus
  stages=canon,det,sem,resolve,dispatch,out,audit; [[ $r == hb-off-* ]] && stages=canon,det,sem,resolve,dispatch,audit
  pol=none; [[ $corpus == correctness-* ]] && pol=enforce
  $PY $EV/tools/pbu_analyze.py --run $d --mode $mode --profile-stages $stages --corpus $cpath --policy $pol > $d/pbu.stdout 2>&1
  mkdir -p $EV/runs/$r; cp $d/pbu_summary.* $EV/runs/$r/
  echo "$r $(head -1 $d/pbu_summary.md)"
}
export -f one; export SP EV RUNS PY
ls $RUNS | xargs -P 6 -I{} bash -c 'one {}'
