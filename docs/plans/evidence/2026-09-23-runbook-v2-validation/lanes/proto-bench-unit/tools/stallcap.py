#!/usr/bin/env python3
"""stallcap.py OUTDIR  (runs ON the unit, until killed)

Diagnostic for the periodic guard-owner stall at hh:m5:10 UTC seen in h22-025 / h22-050.
Every 10 minutes from hh:m5:03 for 13 s it records, without pausing the SUT (one process, no forks
in the sampling loop):
  threads.jsonl  every ~10 ms: state, utime, stime, kernel wait channel of every guard-owner thread
                 and of the main thread of 3 workers (a GIL wait shows as futex_*, disk as io_schedule...)
  psi.jsonl      every ~50 ms: /proc/pressure/{io,cpu,memory} and /proc/diskstats for the boot disk
  pyspy.txt      every ~150 ms: `py-spy dump --nonblocking` of each owner (separate subprocess)
  journal.txt    the system journal of the window
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def pids(pattern: str) -> list[int]:
    out = []
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                cmd = Path(f"/proc/{d}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            except OSError:
                continue
            if pattern in cmd and "stallcap" not in cmd:
                out.append(int(d))
    return sorted(out)


def read(p: str) -> str:
    try:
        return Path(p).read_text()
    except OSError:
        return ""


def capture(d: Path, dur: float) -> None:
    owners = pids("rvproto.serve --guard-owner")
    workers = pids("rvproto.serve --worker")[:3]
    (d / "pids.json").write_text(json.dumps({"owners": owners, "workers": workers}))
    spy = subprocess.Popen(["bash", "-c", f"end=$(( $(date +%s) + {int(dur)} )); while [[ $(date +%s) -lt $end ]]; do "
                            + " ".join(f"echo \"=== $(date +%s.%N) owner {p}\"; sudo /home/rv/pyspy/bin/py-spy dump --nonblocking --pid {p} 2>&1 | head -80;"
                                       for p in owners)
                            + " sleep 0.15; done"], stdout=open(d / "pyspy.txt", "w"), stderr=subprocess.STDOUT)
    t_end = time.time() + dur
    tf = open(d / "threads.jsonl", "w")
    pf = open(d / "psi.jsonl", "w")
    last_psi = 0.0
    while time.time() < t_end:
        t = time.time()
        rows = []
        for p in owners:
            for tid in os.listdir(f"/proc/{p}/task") if os.path.isdir(f"/proc/{p}/task") else []:
                st = read(f"/proc/{p}/task/{tid}/stat")
                f = st.rsplit(")", 1)[1].split() if ")" in st else []
                rows.append([p, int(tid), f[0] if f else "?", int(f[11]) if f else 0, int(f[12]) if f else 0,
                             read(f"/proc/{p}/task/{tid}/wchan"), read(f"/proc/{p}/task/{tid}/comm").strip()])
        for p in workers:
            st = read(f"/proc/{p}/stat")
            f = st.rsplit(")", 1)[1].split() if ")" in st else []
            rows.append([p, p, f[0] if f else "?", int(f[11]) if f else 0, int(f[12]) if f else 0,
                         read(f"/proc/{p}/wchan"), "worker-main"])
        tf.write(json.dumps({"t": t, "th": rows}) + "\n")
        if t - last_psi >= 0.05:
            last_psi = t
            disk = [l for l in read("/proc/diskstats").splitlines() if l.split()[2] in ("sda", "nvme0n1")]
            pf.write(json.dumps({"t": t, "io": read("/proc/pressure/io"), "cpu": read("/proc/pressure/cpu"),
                                 "mem": read("/proc/pressure/memory"), "disk": disk}) + "\n")
        time.sleep(0.01)
    tf.close()
    pf.close()
    spy.wait(timeout=30)
    t0 = int(t_end - dur)
    subprocess.run(["bash", "-c", f"sudo journalctl --since @{t0} --until @{int(t_end)} --no-pager -o short-precise"],
                   stdout=open(d / "journal.txt", "w"), stderr=subprocess.STDOUT)


def main() -> int:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    while True:
        now = int(time.time())
        if (now // 60) % 10 == 5 and 3 <= now % 60 < 6:
            d = out / str(now)
            d.mkdir(parents=True, exist_ok=True)
            capture(d, 13.0)
            time.sleep(60)
        time.sleep(0.5)


if __name__ == "__main__":
    raise SystemExit(main())
