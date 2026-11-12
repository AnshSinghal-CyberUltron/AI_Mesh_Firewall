#!/usr/bin/env bash
# Exactness suites (copy of xlate5/run_r5b.sh, parameterised) against a SNAPSHOT of the translator.
#
#   run_suite.sh SNAPSHOT_RUN_N PYTHON [SEEDS]
#     SNAPSHOT_RUN_N  e.g. 2 -> snapshots xlate5/{translate_prototype,incremental,_cased_table,...}.py
#                     into xlate5/clock/run-2/ (must not exist yet), records sha256, runs from there.
#     PYTHON          interpreter whose `regex` is under test (e.g. clock/venv-tcpu/bin/python)
#     SEEDS           fuzz / incremental-fuzz seeds (default "11 12 13", as run_r5b.sh)
#   RUN_DIR=<existing dir with SHA256SUMS>  reuse a snapshot instead of taking one (verified first).
#   SUITE=r5b (default)  the run_r5b.sh steps.
#   SUITE=r6             also the revision-6 exactness steps of run_r6.sh (gen_cased --check, nesting_test,
#                        estimate_test x3, s1_test, alt_negated, icase_exhaustive); run_r6.sh's timing
#                        benchmarks (s1_hold, drr_bound, inline_bucket6, perf6, stall6) are not exactness
#                        checks and are not run.
#
# Writes r_*.txt, META.txt and done.txt into the run folder. Run it under the heavy lock:
#   flock /home/claude/work/.heavy.lock nice -n 10 clock/run_suite.sh 2 clock/venv-tcpu/bin/python
set -u
X=${XLATE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
C=$X/clock
# Absolute path WITHOUT resolving symlinks: a venv's bin/python is a symlink to the base interpreter,
# and resolving it (readlink -f) silently runs the suites on the base interpreter (12 Nov: run-2 failed so).
N=$1; P="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"; SEEDS=${3:-"11 12 13"}
"$P" -c 'import regex' || { echo "$P cannot import regex" >&2; exit 1; }
FILES="translate_prototype.py incremental.py _cased_table.py fuzz_gen.py fuzz.py incremental_fuzz.py exhaustive.py exhaustive_boundary.py alt_exhaustive.py icase_exhaustive.py"
SUITE=${SUITE:-r5b}
[ "$SUITE" = r6 ] && FILES="$FILES gen_cased.py nesting_test.py estimate_test.py s1_test.py alt_negated.py"
if [ -n "${RUN_DIR:-}" ]; then
    D=$(readlink -f "$RUN_DIR")
    (cd "$D" && sha256sum -c --quiet SHA256SUMS) || { echo "snapshot $D does not match its SHA256SUMS" >&2; exit 1; }
else
    D=$C/run-$N
    [ -e "$D" ] && { echo "$D exists; pick a new run number" >&2; exit 1; }
    mkdir -p "$D"
    LIMITS="no"; [ -e "$X/LIMITS6_SWAPPED.txt" ] && LIMITS="yes (LIMITS6_SWAPPED.txt present at snapshot)"
    for f in $FILES; do cp -p "$X/$f" "$D/"; done
    (cd "$D" && sha256sum $FILES > SHA256SUMS)
    # a swap during the copy would show as a difference here
    (cd "$X" && sha256sum $FILES) | diff - "$D/SHA256SUMS" > /dev/null || { echo "translator changed during snapshot; retry" >&2; exit 1; }
    echo "limits changes included: $LIMITS" > "$D/LIMITS.txt"
fi
cd "$D"
export PYTHONDONTWRITEBYTECODE=1
{
    echo "started $(date -u +%FT%TZ)"
    echo "python: $P"
    "$P" -c 'import sys, regex, importlib.metadata as m; print("regex", m.version("regex"), regex._regex.__file__); print(sys.version)'
    "$P" -c 'import hashlib, regex._regex as r; print("_regex .so sha256", hashlib.sha256(open(r.__file__,"rb").read()).hexdigest())'
    cat LIMITS.txt 2>/dev/null
    echo "seeds: $SEEDS"
    echo "suite: $SUITE"
} > META.txt 2>&1
rm -f done.txt
if [ "$SUITE" = r6 ]; then
    "$P" gen_cased.py --check > r_table.txt 2>&1; echo "gen_cased --check exit $?" >> r_table.txt
    "$P" nesting_test.py 20000 > r_nesting.txt 2>&1
    : > r_estimate.txt; for s in 1 2 3; do "$P" estimate_test.py $s 3400 >> r_estimate.txt 2>&1; done
    "$P" s1_test.py > r_s1_test.txt 2>&1
    "$P" alt_negated.py > r_alt_negated.txt 2>&1
    "$P" icase_exhaustive.py 0 1 > r_icase.txt 2>&1
fi
"$P" exhaustive.py > r_exhaustive.txt 2>&1
"$P" exhaustive_boundary.py > r_boundary.txt 2>&1
"$P" alt_exhaustive.py > r_alt.txt 2>&1
: > r_fuzz.txt; for s in $SEEDS; do "$P" fuzz.py $s 6000 2>&1 | grep -v Warning | grep -v '^HANG' | tail -3 >> r_fuzz.txt; done
: > r_inc.txt; for s in $SEEDS; do "$P" incremental_fuzz.py $s 6000 2>&1 | grep -v Warning | tail -2 >> r_inc.txt; done
echo "finished $(date -u +%FT%TZ)" >> META.txt
echo done > done.txt
