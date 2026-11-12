#!/usr/bin/env bash
# Revision-6 regression and measurements, on the revision-6 translator in this directory.
#
#   P=<python with regex 2023.8.8> run_r6.sh
#
# Run it under the heavy lock:  flock /home/claude/work/.heavy.lock nice -n 10 ./run_r6.sh
# Every step records its own start/end time and the host load in r6_progress.txt; r6_done.txt is
# written last, so a missing r6_done.txt means the run did not finish and its numbers are not cited.
# Revision 6 (12 Nov) re-run: path-independent; adds exhaustive.py and exhaustive_boundary.py (the
# translator changed after the revision-5 r5b run), drr_bound.py and perf6.py, and records META.
set -u
cd "$(dirname "$(readlink -f "$0")")"
P=${P:-../venv-stock/bin/python}
export PYTHONDONTWRITEBYTECODE=1
rm -f r6_done.txt
step() {  # step NAME OUTFILE CMD...
    local name=$1 out=$2; shift 2
    local t0; t0=$(date +%s)
    echo "start $name $(date -u +%T) load $(cut -d' ' -f1-3 /proc/loadavg)" >> r6_progress.txt
    "$@" > "$out" 2>&1; local rc=$?
    echo "end   $name $(date -u +%T) exit $rc $(( $(date +%s) - t0 )) s: $(grep -v '^\s*$' "$out" | tail -1 | cut -c1-160)" >> r6_progress.txt
}
loop() {  # loop SCRIPT ARGS... over seeds 11 12 13, keeping each seed's summary
    local s; for s in 11 12 13; do "$P" "$1" $s 6000 2>&1 | grep -v Warning | grep -v '^HANG' | tail -3; done
}
{
    echo "started $(date -u +%FT%TZ) on $(hostname), $(nproc) CPUs"
    "$P" -c 'import sys, regex, importlib.metadata as m, hashlib, regex._regex as r; print("python", sys.version.split()[0]); print("regex", m.version("regex"), r.__file__); print("_regex .so sha256", hashlib.sha256(open(r.__file__,"rb").read()).hexdigest())'
    sha256sum translate_prototype.py incremental.py _cased_table.py
} > r6_META.txt 2>&1
echo "lock acquired $(date -u +%FT%TZ)" > r6_progress.txt
# Exactness regression on the revision-6 translator.
step exhaustive r6_exhaustive.txt "$P" exhaustive.py
step exhaustive_boundary r6_boundary.txt "$P" exhaustive_boundary.py
step alt_exhaustive r6_alt.txt "$P" alt_exhaustive.py
step fuzz r6_fuzz.txt loop fuzz.py
step incremental_fuzz r6_inc.txt loop incremental_fuzz.py
step icase r6_icase.txt "$P" icase_exhaustive.py 0 1
# The revision-6 tests.
step gen_cased_check r6_table.txt "$P" gen_cased.py --check
step alt_negated r6_alt_negated.txt "$P" alt_negated.py
step nesting r6_nesting.txt "$P" nesting_test.py 20000
step estimate r6_estimate.txt bash -c 'for s in 1 2 3; do "$0" estimate_test.py $s 3400; done' "$P"
step s1_test r6_s1_test.txt "$P" s1_test.py
step s1_hold r6_s1_hold.txt bash -c 'for s in 1 2 3; do "$0" s1_hold.py $s 2000 1; done; "$0" s1_hold.py 4 2000 10' "$P"
# Measurements (timing; the heavy lock keeps other heavy jobs off the host, light ones still run).
step drr_bound r6_drr.txt "$P" drr_bound.py
step inline_bucket6 r6_inline_locked.txt "$P" inline_bucket6.py 3 3
step stall6 r6_stall.txt "$P" stall6.py 3
step perf6 r6_perf6.log "$P" perf6.py
ps -eo pcpu,args --sort=-pcpu | head -8 | cut -c1-140 > r6_top_at_end.txt
echo "finished $(date -u +%FT%TZ)" >> r6_META.txt
echo "done $(date -u +%FT%TZ)" > r6_done.txt
