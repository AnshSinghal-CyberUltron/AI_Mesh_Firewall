#!/bin/bash
# Validated-binary cross-check (team-lead request): claim 1 at one equal rate per variant (318 req/s, 1 worker pinned,
# directly comparable with the main w1_*_r318 runs) and claim 3 keepalive at 30,000 req/s (main-run knee).
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
E=$SP/evidence/micro-claims; X=$E/xcheck
export OVERRIDE_ENV=$X/xcheck.env RUNS_SUBDIR=xcheck_runs
source $X/xcheck.env
( for v in none pure4 base4 v1stack; do $E/claim1/c1_step.sh $SUT3 $LG6 xc_w1_${v}_r318 $v 1 2 318; done ) > $X/claim1_xcheck.log 2>&1 &
$E/claim3/c3_step.sh xc_c3_B_keepalive_r30000 B_keepalive 30000 > $X/claim3_xcheck.log 2>&1 &
wait
echo "XCHECK DONE $(date -u +%FT%TZ)" >> $X/claim1_xcheck.log
