#!/bin/bash
P=$1
while read -r label emits mode level netem rate replay; do
  [ -z "$label" ] || [ "${label:0:1}" = "#" ] && continue
  /tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/micro-claims/claim2/c/c2c_step.sh $label $emits $mode $level $netem $rate $replay
done < $P
echo "PLAN DONE $P $(date -u +%FT%TZ)"
