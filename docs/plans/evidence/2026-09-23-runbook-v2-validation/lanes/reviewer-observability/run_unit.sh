#!/bin/bash
# reviewer-observability: recompute every complete unit run (PUB/C/ABC views, client C4 view, gateway deltas)
RO=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/reviewer-observability
PY=/home/contact_cyberultron_com/AI_Mesh_Firewall/gateway/.venv/bin/python
RAW=$HOME/rv-evidence-raw/proto-bench-unit/runs
CORP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/harness/corpora
mkdir -p $RO/out/unit $RO/out/c4
for r in "$@"; do
  d=$RAW/$r
  [ -f $d/pbu_summary.json ] || { echo "skip $r (no pbu_summary)"; continue; }
  mode=sut; extra=""; c4x=""
  case $r in d-*) mode=direct; c4x=--direct;; esac
  case $r in c22-*) extra="--corpus $CORP/correctness-22M.jsonl";; esac
  nice -n 19 $PY $RO/recompute.py $d --mode $mode $extra --out $RO/out/unit/$r.json > /dev/null
  nice -n 19 $PY $RO/c4_client.py $d $c4x > $RO/out/c4/$r.json
  U=$d/rv-proto-unit-1/unit
  if [ -d $U/snap-meas_start ] && [ -d $U/snap-meas_end ]; then
    $PY $RO/gwsnap.py $U/snap-meas_start $U/snap-meas_end > $RO/out/unit/$r.gw.meas.json
    $PY $RO/gwsnap.py $U/snap-pre $U/snap-post > $RO/out/unit/$r.gw.whole.json
  fi
  echo "done $r"
done
