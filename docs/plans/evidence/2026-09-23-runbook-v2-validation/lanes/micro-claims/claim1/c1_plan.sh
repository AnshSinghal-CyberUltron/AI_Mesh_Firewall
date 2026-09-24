#!/bin/bash
# c1_plan.sh PLANFILE SUT LG : run every step in PLANFILE (label variant workers cpus rate), skipping done ones
P=$1 SUT=$2 LG=$3
while read -r label variant workers cpus rate; do
  [ -z "$label" ] || [ "${label:0:1}" = "#" ] && continue
  /tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/micro-claims/claim1/${STEP:-c1w_step.sh} $SUT $LG $label $variant $workers $cpus $rate
done < $P
echo "PLAN DONE $P $(date -u +%FT%TZ)"
