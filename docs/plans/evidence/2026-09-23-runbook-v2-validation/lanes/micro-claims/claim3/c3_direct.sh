#!/bin/bash
# Claim-3 direct floors (olg -> upstream, no nginx) at the rates that matter, after planner 2 was stopped because
# further B repeats at >=30k req/s were only producing loadgen-drop-invalidated runs with this olg build.
D=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/micro-claims/claim3
while pgrep -f "claim3/c3_[s]tep.sh" > /dev/null; do sleep 10; done
for r in 400 5000 20000; do $D/c3_step.sh c3_direct_r$r direct $r; done
echo "DIRECT DONE $(date -u +%FT%TZ)"
