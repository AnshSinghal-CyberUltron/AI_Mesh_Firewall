#!/usr/bin/env bash
# Revision-7 regression and measurements, on the revision-7 translator in this directory and the
# revision-7 candidate wheel (clock + match-memory-cap + upstream 611-614 patches, clock/build_patched_regex.sh).
#
#   P=<candidate python> P6=<revision-6 clock-only python> PCI=<gateway_v2 venv python> \
#   WT=<worktree> PA=<ASAN candidate python> run_r7.sh
#
# Run it under the heavy lock:  flock /home/claude/work/.heavy.lock nice -n 10 ./run_r7.sh
# Every step records its start/end time, exit code and the host load in r7_progress.txt; r7_done.txt
# is written last, so a missing r7_done.txt means the run did not finish and its numbers are not cited.
set -u
cd "$(dirname "$(readlink -f "$0")")"
: "${P:?candidate python}" "${P6:?revision-6 python}" "${PCI:?gateway_v2 python}" "${WT:?worktree}" "${PA:?ASAN python}"
export PYTHONDONTWRITEBYTECODE=1
rm -f r7_done.txt
step() {  # step NAME OUTFILE CMD...
    local name=$1 out=$2; shift 2
    local t0; t0=$(date +%s)
    echo "start $name $(date -u +%T) load $(cut -d' ' -f1-3 /proc/loadavg)" >> r7_progress.txt
    "$@" > "$out" 2>&1; local rc=$?
    echo "end   $name $(date -u +%T) exit $rc $(( $(date +%s) - t0 )) s: $(grep -v '^\s*$' "$out" | tail -1 | cut -c1-160)" >> r7_progress.txt
}
loop() {  # loop SCRIPT: seeds 11 12 13 x 6000 patterns, each seed's summary
    local s; for s in 11 12 13; do "$P" "$1" $s 6000 2>&1 | grep -v Warning | grep -v '^HANG' | tail -3; done
}
{
    echo "started $(date -u +%FT%TZ) on $(hostname), $(nproc) CPUs"
    for py in "$P" "$P6" "$PA"; do
        "$py" -c 'import sys, regex, importlib.metadata as m, hashlib, regex._regex as r; print(sys.executable, "python", sys.version.split()[0], "regex", m.version("regex"), "_regex .so sha256", hashlib.sha256(open(r.__file__,"rb").read()).hexdigest(), "memcap", hasattr(r, "set_match_memory_limit"))'
    done
    sha256sum translate_prototype.py incremental.py _cased_table.py gen_cased.py clock/*.patch
} > r7_META.txt 2>&1
echo "lock acquired $(date -u +%FT%TZ)" > r7_progress.txt
# --- exactness on the candidate wheel, revision-7 translator --------------------------------------
step gen_cased_check r7_table.txt "$P" gen_cased.py --check
step ci_clip r7_ci_clip.txt "$P" ci_clip_test.py 1 20000
step exhaustive r7_exhaustive.txt "$P" exhaustive.py
step exhaustive_boundary r7_boundary.txt "$P" exhaustive_boundary.py
step icase r7_icase.txt "$P" icase_exhaustive.py 0 1
step alt_exhaustive r7_alt.txt "$P" alt_exhaustive.py
step alt_negated r7_alt_negated.txt "$P" alt_negated.py
step fuzz r7_fuzz.txt loop fuzz.py
step incremental_fuzz r7_inc.txt loop incremental_fuzz.py
step renumber r7_renumber.txt bash -c 'for s in 1 2 3; do "$0" renumber_test.py $s 3000; done' "$P"
step nesting r7_nesting.txt "$P" nesting_test.py 20000
step estimate r7_estimate.txt bash -c 'for s in 1 2 3; do "$0" estimate_test.py $s 3400; done' "$P"
step s1_test r7_s1_test.txt "$P" s1_test.py
step upstream_tests r7_upstream_tests.txt bash -c 'for cap in 0 16777216; do "$0" -c "
import regex._regex as R, unittest
R.set_match_memory_limit($cap)
import regex.test_regex as t
r = unittest.main(module=t, exit=False, argv=[\"x\"], verbosity=0).result
print(\"match-memory cap\", $cap, \"tests run\", r.testsRun, \"failures\", len(r.failures), \"errors\", len(r.errors))"; done' "$P"
# --- the revision-7 measurements ------------------------------------------------------------------
step ci_model r7_ci_model.txt "$P" ci_model7.py 4 300
step ci_cost r7_ci_cost.txt "$P" ci_cost7.py ../2026-11-12-f1-plan-rev6 .
step mem_corpus r7_mem_corpus.txt bash -c 'ulimit -v 3000000; "$0" mem7.py corpus' "$P"
step mem_nocap r7_mem_nocap.txt bash -c 'ulimit -v 3000000; "$0" mem7.py all 0' "$P"
step mem_cap16 r7_mem_cap16.txt bash -c 'ulimit -v 3000000; "$0" mem7.py all 16777216' "$P"
step mem_speed r7_mem_speed.txt bash -c 'for i in 1 2 3; do "$1" mem7.py speed; "$0" mem7.py speed; done' "$P" "$P6"
step cross_tenant r7_cross_tenant.txt "$P" clock/probes/cross_tenant.py 20 3 131072
step s1_hold r7_s1_hold.txt "$P" s1_hold7.py 100
step rewrite_check r7_rewrite_check.txt "$P" rewrite_check7.py
step idle r7_idle.txt env PYTHONPATH="$WT/gateway_v2:$WT/shared" "$PCI" idle7.py
step inline_fair r7_inline_fair.txt "$P" inline_fair7.py 3 3 "0.25:1:avail,0.25:1:avail:bypass,0.25:2:avail,0.25:2:avail:bypass,0.25:4:avail:bypass,0.25:6:avail:bypass" .
step drr_fast r7_drr_fast.txt "$P" drr_fast7.py
step cost r7_cost.txt "$P" cost7.py
step cache r7_cache.txt "$P" cache7.py
step check_plan r7_check_plan.txt "$P" check_plan7.py
# --- crash fuzz on the ASAN + UBSan build of the candidate (positive control first) ---------------
rm -rf r7_asan && mkdir -p r7_asan
step asan r7_asan_summary.txt "$PA" clock/probes/asan_fuzz.py r7_asan 900 2
ps -eo pcpu,args --sort=-pcpu | head -8 | cut -c1-140 > r7_top_at_end.txt
echo "finished $(date -u +%FT%TZ)" >> r7_META.txt
echo "done $(date -u +%FT%TZ)" > r7_done.txt
