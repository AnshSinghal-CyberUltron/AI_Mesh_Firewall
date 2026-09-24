#!/usr/bin/env bash
# reviewer-observability / v1: recompute every v1-bench run from raw with recompute.py.
#  sut runs : (1) lg-v1aug (lane's audit-joined copy: disp/stages from v1's own audit events) --mode sut, v1 nine stages
#             (2) raw olg lg --mode direct  = WIRE-ONLY qualification (200 + [DONE]/JSON + 1 provider call + sha equal)
#  direct   : raw lg --mode direct
RO=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/reviewer-observability
V=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs
PY=/home/contact_cyberultron_com/AI_Mesh_Firewall/gateway/.venv/bin/python
ST=auth,kill_switch,rate_limit,policy,input_scan,model_routing,model_input,model_output,output_guardrail
for run in "$@"; do
  R=$V/$run
  if [ -d "$R/lg-v1aug" ]; then
    nice -n 19 $PY $RO/recompute.py $R --mode sut --stages $ST --lg-glob 'lg-v1aug' --prov-glob 'rv-v1-prov-*/prov' --out $RO/v1/out/$run.audit.json > /dev/null
    nice -n 19 $PY $RO/recompute.py $R --mode direct --lg-glob 'rv-v1-lg-*/lg' --prov-glob 'rv-v1-prov-*/prov' --out $RO/v1/out/$run.wire.json > /dev/null
  else
    nice -n 19 $PY $RO/recompute.py $R --mode direct --lg-glob 'rv-v1-lg-*/lg' --prov-glob 'rv-v1-prov-*/prov' --out $RO/v1/out/$run.wire.json > /dev/null
  fi
  echo "done $run"
done
