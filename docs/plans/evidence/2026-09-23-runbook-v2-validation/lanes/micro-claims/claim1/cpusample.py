#!/usr/bin/env python3
"""Per-second CPU sampler. JSONL rows: {t, hz, ticks (utime+stime of procs whose cmdline contains PATTERN),
n_procs, all_ticks (utime+stime summed over EVERY process incl. kernel threads), sched_run_ns (sum over CPUs of
/proc/schedstat field 7 = ns tasks ran), ncpu, stat (/proc/stat aggregate line)}.
NOTE: on these tickless GCP kernels (NO_HZ_FULL + VIRT_CPU_ACCOUNTING_GEN) the /proc/stat busy columns miss short
bursts (verified: 0.15 cores of uvicorn showed as ~0 in /proc/stat), while per-task utime/stime and schedstat come
from precise runtime accounting -> use ticks / all_ticks / sched_run_ns.
usage: cpusample.py PATTERN OUTFILE SECONDS"""
import os, sys, time, json
pat, out, secs = sys.argv[1], sys.argv[2], float(sys.argv[3])
hz = os.sysconf("SC_CLK_TCK")
def snap():
    # match PATTERN in cmdline, PLUS all descendants of matched processes (uvicorn --workers N spawns workers via
    # multiprocessing whose cmdline does not contain the pattern)
    procs = {}
    for p in os.listdir("/proc"):
        if not p.isdigit(): continue
        try:
            f = open(f"/proc/{p}/stat").read().rsplit(")", 1)[1].split()
            cmd = open(f"/proc/{p}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace")
            procs[int(p)] = (int(f[1]), int(f[11]) + int(f[12]), cmd)   # ppid, utime+stime, cmdline
        except (FileNotFoundError, ProcessLookupError, IndexError, ValueError):
            pass
    allt = sum(v[1] for v in procs.values())
    matched = {pid for pid, (pp, t, cmd) in procs.items() if pat in cmd and "cpusample" not in cmd}
    changed = True
    while changed:
        changed = False
        for pid, (pp, t, cmd) in procs.items():
            if pid not in matched and pp in matched and "cpusample" not in cmd:
                matched.add(pid); changed = True
    ticks = sum(procs[pid][1] for pid in matched); n = len(matched)
    run_ns, ncpu = 0, 0
    for line in open("/proc/schedstat"):
        if line.startswith("cpu"):
            run_ns += int(line.split()[7]); ncpu += 1
    stat = open("/proc/stat").readline().split()[1:]
    return {"t": time.time(), "hz": hz, "ticks": ticks, "n_procs": n, "all_ticks": allt, "sched_run_ns": run_ns,
            "ncpu": ncpu, "stat": [int(x) for x in stat]}
end = time.time() + secs
with open(out, "w") as fo:
    while time.time() < end:
        fo.write(json.dumps(snap()) + "\n"); fo.flush()
        time.sleep(1.0 - (time.time() % 1.0))
