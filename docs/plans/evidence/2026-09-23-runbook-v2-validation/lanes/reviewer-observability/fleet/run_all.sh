#!/bin/bash
# reviewer-observability/fleet: recompute every complete fleet run from raw (read-only on the lane's data)
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
RO=$SP/evidence/reviewer-observability; FO=$RO/fleet; RAW=~/rv-evidence-raw/proto-bench-fleet/runs
PY=/home/contact_cyberultron_com/AI_Mesh_Firewall/gateway/.venv/bin/python
for d in $SP/evidence/proto-bench-fleet/runs/*/; do
  r=$(basename $d)
  [ -f $d/pbf_summary.json ] || continue
  [ -f $FO/out/$r.json ] && continue
  nice -n 19 $PY $RO/recompute.py $RAW/$r --lg-glob 'rv-pbf-lg-*/lg' --prov-glob 'rv-pbf-prov-*/prov' --out $FO/out/$r.json > /dev/null 2> $FO/out/$r.err || echo "FAIL $r"
done
