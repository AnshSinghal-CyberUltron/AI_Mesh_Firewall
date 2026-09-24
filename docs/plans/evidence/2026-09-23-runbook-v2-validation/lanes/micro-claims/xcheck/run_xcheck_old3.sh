#!/bin/bash
# Same-VM control for the cross-check: the OLD olg (df3209a2f020) on the SAME new claim-3 trio, so binary effects can
# be separated from VM-to-VM variance.
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
E=$SP/evidence/micro-claims; X=$E/xcheck
export OVERRIDE_ENV=$X/xcheck.env RUNS_SUBDIR=xcheck_runs
$E/claim3/c3_step.sh xcold_c3_B_keepalive_r30000 B_keepalive 30000
echo "XCOLD3 DONE $(date -u +%FT%TZ)"
