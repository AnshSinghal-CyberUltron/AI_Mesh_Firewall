#!/usr/bin/env bash
# reanalyze_ready.sh — re-score every v1-bench run with the READY analyze.py (sha256 3874eaac...),
# v1 disposition extractor (final_action + v1_stages from v1's own audit events), new strata.
# Raw files are never modified; outputs: runs/<run>/lg-v1ready/ and runs/<run>/analysis-ready/.
set -uo pipefail
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
E=$SP/evidence/v1-bench
PY=/home/contact_cyberultron_com/AI_Mesh_Firewall/gateway/.venv/bin/python
A=$SP/harness/analyze.py
STAGES=auth,kill_switch,rate_limit,policy,input_scan,model_routing,model_input,model_output,output_guardrail
echo "analyze.py sha256: $(sha256sum $A | cut -c1-64)  harness git $(git -C $SP/harness rev-parse --short HEAD)"
for run in "$@"; do
  R=$E/runs/$run
  lg=$(ls -d $R/rv-v1-lg-*/lg | head -1); pv=$(ls -d $R/rv-v1-prov-*/prov | head -1)
  if [ -e "$R/sut/v1-events.jsonl.zst" ]; then
    rm -rf $R/lg-v1ready $R/analysis-ready
    $PY $E/setup/v1_adapter.py --fields v1 --olg $lg --events <(zstdcat $R/sut/v1-events.jsonl.zst) --out-dir $R/lg-v1ready > $R/adapter-ready.txt
    $PY $A --client $R/lg-v1ready --provider $pv --mode sut --disposition-source v1 --profile-stages $STAGES \
       --corpus $SP/harness/corpora/headline-22M.jsonl --out $R/analysis-ready > $R/analyze-ready.txt 2>&1
  else
    rm -rf $R/analysis-ready
    $PY $A --client $lg --provider $pv --mode direct --out $R/analysis-ready > $R/analyze-ready.txt 2>&1
  fi
  echo "$run: $(head -1 $R/analyze-ready.txt)"
done
