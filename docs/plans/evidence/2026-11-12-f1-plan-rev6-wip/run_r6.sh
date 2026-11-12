set -u
cd /tmp/claude-0/-home-claude/2a18a307-83e0-5467-9172-0939d20bde00/scratchpad/xlate5
P=../rv2023.8.8/bin/python
rm -f r6_done.txt
echo "lock acquired $(date -u +%FT%TZ)" > r6_progress.txt
# The regression the coordinator asked for, first.
$P alt_exhaustive.py > r6_alt.txt 2>&1; echo "step alt_exhaustive $(date -u +%T): $(tail -1 r6_alt.txt)" >> r6_progress.txt
: > r6_fuzz.txt; for s in 11 12 13; do $P fuzz.py $s 6000 2>&1 | grep -v Warning | grep -v '^HANG' | tail -3 >> r6_fuzz.txt; done; echo "step fuzz $(date -u +%T)" >> r6_progress.txt
: > r6_inc.txt; for s in 11 12 13; do $P incremental_fuzz.py $s 6000 2>&1 | grep -v Warning | tail -2 >> r6_inc.txt; done; echo "step inc $(date -u +%T)" >> r6_progress.txt
$P icase_exhaustive.py 0 1 > r6_icase.txt 2>&1; echo "step icase $(date -u +%T): $(tail -1 r6_icase.txt)" >> r6_progress.txt
# The revision-6 tests and measurements.
$P gen_cased.py --check > r6_table.txt 2>&1; echo "gen_cased --check exit $?" >> r6_table.txt
$P alt_negated.py > r6_alt_negated.txt 2>&1; echo "step alt_negated $(date -u +%T): $(tail -1 r6_alt_negated.txt)" >> r6_progress.txt
$P nesting_test.py 20000 > r6_nesting.txt 2>&1; echo "step nesting $(date -u +%T): $(tail -1 r6_nesting.txt)" >> r6_progress.txt
: > r6_estimate.txt; for s in 1 2 3; do $P estimate_test.py $s 3400 >> r6_estimate.txt 2>&1; done; echo "step estimate $(date -u +%T)" >> r6_progress.txt
$P s1_test.py > r6_s1_test.txt 2>&1
: > r6_s1_hold.txt; for s in 1 2 3; do $P s1_hold.py $s 2000 1 >> r6_s1_hold.txt 2>&1; done; $P s1_hold.py 4 2000 10 >> r6_s1_hold.txt 2>&1; echo "step s1_hold $(date -u +%T)" >> r6_progress.txt
$P inline_bucket6.py 3 3 > r6_inline_locked.txt 2>&1; echo "step inline $(date -u +%T)" >> r6_progress.txt
$P stall6.py 3 > r6_stall.txt 2>&1
echo "done $(date -u +%FT%TZ)" > r6_done.txt
