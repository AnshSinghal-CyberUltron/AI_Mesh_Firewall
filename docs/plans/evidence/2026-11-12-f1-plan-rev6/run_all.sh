set -u
cd /tmp/claude-0/-home-claude/2a18a307-83e0-5467-9172-0939d20bde00/scratchpad/xlate5
P=../rv2023.8.8/bin/python
rm -f log_done.txt
$P exhaustive.py > log_exhaustive.txt 2>&1
$P exhaustive_boundary.py > log_exhaustive_boundary.txt 2>&1
$P icase_exhaustive.py 0 1 > log_icase.txt 2>&1
$P alt_exhaustive.py > log_alt.txt 2>&1
: > log_fuzz.txt; for s in 1 2 3 4 5 6 7 8 303; do $P fuzz.py $s 6000 2>&1 | grep -v Warning | tail -3 >> log_fuzz.txt; done
: > log_incremental.txt; for s in 1 2 3 4 5 6 7 8; do $P incremental_fuzz.py $s 6000 2>&1 | grep -v Warning | tail -3 >> log_incremental.txt; done
echo ALLDONE > log_done.txt
