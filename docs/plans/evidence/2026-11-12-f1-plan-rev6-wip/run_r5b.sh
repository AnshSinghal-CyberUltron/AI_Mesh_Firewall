set -u
cd /tmp/claude-0/-home-claude/2a18a307-83e0-5467-9172-0939d20bde00/scratchpad/xlate5
P=../rv2023.8.8/bin/python
rm -f r5b_done.txt
$P exhaustive.py > r5b_exhaustive.txt 2>&1
$P exhaustive_boundary.py > r5b_boundary.txt 2>&1
$P alt_exhaustive.py > r5b_alt.txt 2>&1
: > r5b_fuzz.txt; for s in 11 12 13; do $P fuzz.py $s 6000 2>&1 | grep -v Warning | grep -v '^HANG' | tail -3 >> r5b_fuzz.txt; done
: > r5b_inc.txt; for s in 11 12 13; do $P incremental_fuzz.py $s 6000 2>&1 | grep -v Warning | tail -2 >> r5b_inc.txt; done
echo done > r5b_done.txt
