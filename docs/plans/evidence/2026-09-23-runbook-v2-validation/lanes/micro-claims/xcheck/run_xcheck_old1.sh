#!/bin/bash
# Same-VM control for claim 1: after the d22d2607 steps finish, swap the OLD olg (df3209a2f020) onto the same loadgen and
# re-run none + base4 at 318 req/s on the same SUT, so binary effects are separated from VM-to-VM variance.
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
E=$SP/evidence/micro-claims; X=$E/xcheck
export OVERRIDE_ENV=$X/xcheck.env RUNS_SUBDIR=xcheck_runs
source $X/xcheck.env
until grep -q "XCHECK DONE" $X/claim1_xcheck.log 2>/dev/null; do sleep 15; done
$E/bin/rssh $LG6 "cp ~/olg ~/olg_d22d2607" < /dev/null
$E/bin/rscp $E/bin/olg_df3209a2f020 rv@$LG6:olg
$E/bin/rssh $LG6 "sha256sum ~/olg ~/olg_d22d2607" < /dev/null
for v in none base4; do $E/claim1/c1_step.sh $SUT3 $LG6 xcold_w1_${v}_r318 $v 1 2 318; done
echo "XCOLD1 DONE $(date -u +%FT%TZ)"
