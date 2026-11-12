#!/usr/bin/env bash
# Revision-6 probes re-run on the 12 Nov host (the 11 Nov host's results are kept in results/).
#
#   run_host2.sh          (from anywhere; run it under the heavy lock)
#
# Needs: ../venv-stock (unpatched pinned build, NOPATCH=1) and venv-tcpu (patched), both built by
# build_patched_regex.sh. Writes results/host2_*.txt and ../r6_range_b1.txt; host2_done.txt last.
set -u
C="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"; X="$(cd "$C/.." && pwd)"
STOCK=$X/venv-stock/bin/python; TCPU=$C/venv-tcpu/bin/python; NOGIL=$C/venv-tcpu-nogil/bin/python
export PYTHONDONTWRITEBYTECODE=1
R=$C/results; rm -f $R/host2_done.txt; : > $R/host2_progress.txt
step() { local name=$1 out=$2; shift 2; local t0; t0=$(date +%s)
  echo "start $name $(date -u +%T) load $(cut -d' ' -f1-3 /proc/loadavg)" >> $R/host2_progress.txt
  { echo "# $(date -u +%FT%TZ)  load average: $(cut -d' ' -f1-3 /proc/loadavg)  cmd: $*"; "$@"; } > "$out" 2>&1; local rc=$?
  echo "end   $name $(date -u +%T) exit $rc $(( $(date +%s) - t0 )) s" >> $R/host2_progress.txt; }
cd "$X"
# Reviewer A, rev. 5, B1: range classifier, revision 6 against the committed revision 5.
step range_b1 "$X/r6_range_b1.txt" bash -c "'$STOCK' range_b1.py . 3; '$STOCK' range_b1.py rev5_e2c3e2cf 3"
# Reviewer B, rev. 5, should-fix 3: the worker-wide bucket refilled by WALL time (B's simulation) against
# refilled by loop-AVAILABLE time (plan 5.2.5), same run, same host.
step inline_avail "$X/r6_inline_avail.txt" "$STOCK" inline_bucket6.py 3 3 "none:4,0.25:2,0.25:4,0.25:6,0.25:2:avail,0.25:4:avail,0.25:6:avail,0.0:4"
cd "$C/probes"
# Reviewer B, rev. 5, blocker 1, on this host: B's three probes, then the cross-tenant test, both wheels.
for w in stock tcpu; do P=$STOCK; [ $w = tcpu ] && P=$TCPU
  step "b_probes_$w" "$R/host2_b_probes_$w.txt" bash -c "cd b && for f in clock_semantics.py clock_t2.py clock_pass.py; do echo \"## \$f\"; '$P' \$f; done"
  step "cross_tenant_$w" "$R/host2_cross_tenant_${w}_k3.txt" "$P" cross_tenant.py 20 3 131072
  step "edge_$w" "$R/host2_edge_$w.txt" "$P" edge_timeouts.py
done
# The two plan changes B-B1 still needs: the GIL convoy (wall) and thread-CPU inflation.
step gil_tcpu "$R/host2_gil_tcpu.txt" "$TCPU" gil_probe.py 384 5
step gil_duty_tcpu "$R/host2_gil_duty_tcpu.txt" "$TCPU" gil_duty.py 384 5
step cpu_variance "$R/host2_cpu_variance.txt" bash -c "'$TCPU' cpu_variance.py 60; '$TCPU' cpu_variance.py 60"
# PROTOTYPE second patch (GIL-free periodic check on non-main threads; not shipped): the convoy and the
# cross-tenant property on it.
step gil_duty_nogil "$R/host2_gil_duty_nogil.txt" "$NOGIL" gil_duty.py 384 5
step cross_tenant_nogil "$R/host2_cross_tenant_nogil_k3.txt" "$NOGIL" cross_tenant.py 20 3 131072
step semantics "$R/host2_semantics.txt" bash -c "for p in '$STOCK' '$TCPU' '$NOGIL'; do echo \"== \$p\"; \"\$p\" -I '$C/probes/semantics_check.py'; done"
step overshoot_tcpu "$R/host2_overshoot_tcpu.txt" "$TCPU" overshoot_mt.py 5
ps -eo pcpu,args --sort=-pcpu | head -8 | cut -c1-140 > $R/host2_top_at_end.txt
echo "done $(date -u +%FT%TZ)" > $R/host2_done.txt
