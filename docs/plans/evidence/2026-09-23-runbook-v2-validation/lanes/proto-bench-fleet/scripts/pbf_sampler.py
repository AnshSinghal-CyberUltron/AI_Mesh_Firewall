#!/usr/bin/env python3
"""SUT-side sampler for one benchmark step (runs ON a unit, the edge, or the shared-Redis VM).
(proto-bench-fleet copy of proto-bench-unit/tools/unit_sampler.py; additions: nginx/synthprov roles,
 iptables ACCT byte counters + redis INFO at every snapshot, optional Redis PING RTT probe.)

  unit_sampler.py --out DIR --start-at UNIX_MS --ramp S --warmup S --duration S [--tail S]

Every second (1 JSON line in DIR/samples.jsonl):
  * per process of the SUT (rvproto launcher / workers / guard owners, redis-server, tritonserver):
    utime+stime (clock ticks, all threads, /proc/PID/stat), exact runtime ns summed over all
    threads (/proc/PID/task/*/schedstat field 1), RSS kB, open fds, threads
  * per CPU: /proc/schedstat run ns + runqueue wait ns; /proc/stat jiffies (kept for comparison:
    tick-sampled, known to under-report on some GCP kernels)
  * ens4 rx/tx bytes+packets (/proc/net/dev), TCP sockstat
Snapshots of the rvproto metrics dir (worker-*.json, owner-*.json: cumulative histograms and
counters, dumped every 1 s by each process) at: pre (now), meas_start, meas_end, post.
nvidia-smi dmon (sm/mem util, fb) runs for the whole step into DIR/dmon.txt when a GPU exists.
All times are unix seconds of this host (only used for alignment with the step schedule).
"""

from __future__ import annotations

import argparse
import re
import glob
import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

HZ = os.sysconf("SC_CLK_TCK")
METRICS = Path(os.path.expanduser("~/rv/metrics"))


def role_of(cmd: str) -> str | None:
    if "rvproto.serve" in cmd:
        if "--worker" in cmd:
            return "worker"
        if "--guard-owner" in cmd:
            return "owner"
        return "launcher"
    if cmd.startswith("/usr/bin/redis-server") or "redis-server" in cmd.split(" ")[0]:
        return "redis"
    if "tritonserver" in cmd.split(" ")[0]:
        return "triton"
    if cmd.startswith("nginx"):
        return "nginx"
    return None


def procs() -> dict[int, str]:
    out = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            cmd = Path(f"/proc/{d}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
        except OSError:
            continue
        r = role_of(cmd)
        if r is not None:
            if r == "owner":
                r = "owner" + cmd.split("--guard-owner", 1)[1].split()[0]
            out[int(d)] = r
    return out


def proc_sample(pid: int) -> dict | None:
    try:
        st = Path(f"/proc/{pid}/stat").read_text()
        fields = st.rsplit(")", 1)[1].split()
        utime, stime, threads = int(fields[11]), int(fields[12]), int(fields[17])
        run_ns = 0
        for t in glob.glob(f"/proc/{pid}/task/*/schedstat"):
            try:
                run_ns += int(Path(t).read_text().split()[0])
            except (OSError, ValueError, IndexError):
                pass
        rss = 0
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                rss = int(line.split()[1])
                break
        try:
            fds = len(os.listdir(f"/proc/{pid}/fd"))
        except OSError:
            fds = -1
        return {"ticks": utime + stime, "run_ns": run_ns, "rss_kb": rss, "fds": fds, "thr": threads}
    except (OSError, IndexError, ValueError):
        return None


def cpu_sample() -> dict:
    sched = {}
    for line in Path("/proc/schedstat").read_text().splitlines():
        if line.startswith("cpu"):
            p = line.split()
            sched[p[0]] = [int(p[7]), int(p[8])]
    stat = {}
    for line in Path("/proc/stat").read_text().splitlines():
        if line.startswith("cpu"):
            p = line.split()
            stat[p[0]] = [int(x) for x in p[1:9]]
    return {"sched": sched, "stat": stat}


def net_sample() -> dict:
    out = {}
    for line in Path("/proc/net/dev").read_text().splitlines()[2:]:
        name, rest = line.split(":", 1)
        name = name.strip()
        if name in ("ens4", "eth0"):
            v = rest.split()
            out[name] = {"rx_bytes": int(v[0]), "rx_pkts": int(v[1]), "tx_bytes": int(v[8]), "tx_pkts": int(v[9])}
    try:
        out["sockstat"] = Path("/proc/net/sockstat").read_text().splitlines()[1]
    except OSError:
        pass
    return out


GAUGES = ("active_streams", "inflight_requests", "input_queue_depth", "guard_inflight_tokens",
          "guard_queue_items", "guard_queue_windows", "audit_queue_depth", "audit_dropped")


def gauge_sample() -> dict:
    """Live gauges from the per-process metric dumps (refreshed every 1 s by each process)."""
    out: dict = {}
    for f in list(METRICS.glob("worker-*.json")) + list(METRICS.glob("owner-*.json")):
        try:
            g = json.loads(f.read_text()).get("gauge") or {}
        except (OSError, ValueError):
            continue
        kind = "o" if f.name.startswith("owner") else "w"
        for k in GAUGES:
            if k in g:
                v = float(g[k])
                out[f"{kind}.{k}.sum"] = out.get(f"{kind}.{k}.sum", 0.0) + v
                out[f"{kind}.{k}.max"] = max(out.get(f"{kind}.{k}.max", 0.0), v)
    return out


REDIS_INFO = False


def snapshot(out: Path, label: str) -> None:
    d = out / f"snap-{label}"
    d.mkdir(parents=True, exist_ok=True)
    try:
        (d / "iptables_acct.txt").write_text(subprocess.run(["sudo", "iptables", "-L", "ACCT", "-v", "-x", "-n"],
                                                            capture_output=True, text=True, timeout=10).stdout)
    except (OSError, subprocess.SubprocessError):
        pass
    (d / "netdev.txt").write_text(Path("/proc/net/dev").read_text())
    try:
        ss = subprocess.run(["ss", "-tnpH", "state", "established", "( sport = :8400 )"], capture_output=True,
                            text=True, timeout=10).stdout
        per_pid: dict = {}
        for line in ss.splitlines():
            for pid in set(re.findall(r"pid=(\d+)", line)):
                per_pid[pid] = per_pid.get(pid, 0) + 1
        (d / "ss_conns.json").write_text(json.dumps({"total": len(ss.splitlines()), "per_pid": per_pid}))
    except (OSError, subprocess.SubprocessError):
        pass
    if REDIS_INFO:
        try:
            (d / "redis_info.txt").write_text(subprocess.run(["redis-cli", "INFO", "all"], capture_output=True,
                                                             text=True, timeout=10).stdout)
        except (OSError, subprocess.SubprocessError):
            pass
    for f in list(METRICS.glob("worker-*.json")) + list(METRICS.glob("owner-*.json")):
        try:
            shutil.copy2(f, d / f.name)
        except OSError:
            pass
    (d / "t.txt").write_text(f"{time.time()}\n")


def redis_probe(host: str, port: int, out: Path, t0: float, t1: float, stop_flag: list) -> None:
    """PING RTT every 10 ms over one persistent TCP connection during [t0, t1]; per-second
    nearest-rank p50/p99/max (us) + all samples' histogram (1 us buckets up to 100 ms)."""
    import socket
    s = socket.create_connection((host, port), timeout=2)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    rows, allv, cur, sec = [], [], [], None
    while not stop_flag[0] and time.time() < t1:
        now = time.time()
        if now < t0:
            time.sleep(min(0.5, t0 - now))
            continue
        a = time.perf_counter_ns()
        s.sendall(b"PING\r\n")
        buf = b""
        while not buf.endswith(b"\r\n"):
            buf += s.recv(64)
        v = (time.perf_counter_ns() - a) / 1000.0
        allv.append(v)
        k = int(now)
        if sec is None:
            sec = k
        if k != sec:
            cur.sort()
            rows.append({"t": sec, "n": len(cur), "p50": cur[len(cur) // 2], "p99": cur[min(len(cur) - 1, int(0.99 * len(cur)))], "max": cur[-1]})
            cur, sec = [], k
        cur.append(v)
        time.sleep(0.01)
    allv.sort()
    n = len(allv)
    summ = {"n": n}
    if n:
        for q in (0.5, 0.9, 0.99, 0.999):
            summ[f"p{q*100:g}_us"] = round(allv[min(n - 1, int(q * n))], 1)
        summ["max_us"] = round(allv[-1], 1)
        summ["mean_us"] = round(sum(allv) / n, 1)
    (out / "redis_ping.json").write_text(json.dumps({"summary": summ, "per_second": rows}))


def main() -> int:
    global METRICS, REDIS_INFO
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--start-at", type=int, required=True, help="olg start, unix ms")
    ap.add_argument("--ramp", type=float, default=30)
    ap.add_argument("--warmup", type=float, default=60)
    ap.add_argument("--duration", type=float, default=300)
    ap.add_argument("--tail", type=float, default=30)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--metrics-dir", default=str(METRICS), help="rvproto RV_METRICS_DIR")
    ap.add_argument("--redis-info", action="store_true", help="snapshot redis-cli INFO all (Redis VM)")
    ap.add_argument("--redis-probe", default="", help="HOST:PORT to PING every 10 ms in the measurement window")
    a = ap.parse_args()
    METRICS = Path(a.metrics_dir)
    REDIS_INFO = a.redis_info
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    t_start = a.start_at / 1000.0
    t_meas0 = t_start + a.ramp + a.warmup
    t_meas1 = t_meas0 + a.duration
    t_end = t_meas1 + a.tail
    (out / "schedule.json").write_text(json.dumps({"start": t_start, "meas_start": t_meas0, "meas_end": t_meas1,
                                                    "end": t_end, "hz": HZ, "ncpu": os.cpu_count()}))
    dmon = None
    if shutil.which("nvidia-smi"):
        fh = open(out / "dmon.txt", "w")
        dmon = subprocess.Popen(["nvidia-smi", "dmon", "-s", "um", "-d", "1", "-o", "T"], stdout=fh,
                                stderr=subprocess.STDOUT, start_new_session=True)
        try:
            (out / "nvidia-smi-q.txt").write_text(subprocess.run(["nvidia-smi", "-q"], capture_output=True,
                                                                 text=True, timeout=30).stdout)
        except (OSError, subprocess.SubprocessError):
            pass
    snapshot(out, "pre")
    snaps = [(t_meas0, "meas_start"), (t_meas1, "meas_end")]
    stop = False
    probe_stop = [False]
    probe = None
    if a.redis_probe:
        import threading
        h, pt = a.redis_probe.rsplit(":", 1)
        probe = threading.Thread(target=redis_probe, args=(h, int(pt), out, t_meas0, t_meas1, probe_stop), daemon=True)
        probe.start()

    def _term(*_: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _term)
    with open(out / "samples.jsonl", "w") as fh:
        nxt = time.time()
        tick = 0
        while not stop and time.time() < t_end:
            now = time.time()
            while snaps and now >= snaps[0][0]:
                snapshot(out, snaps.pop(0)[1])
            ps = {}
            for pid, role in procs().items():
                s = proc_sample(pid)
                if s is not None:
                    s["role"] = role
                    ps[str(pid)] = s
            rec = {"t": now, "procs": ps, "cpu": cpu_sample(), "net": net_sample(),
                   "load": Path("/proc/loadavg").read_text().split()[:3]}
            if tick % 5 == 0:
                rec["gauges"] = gauge_sample()
            tick += 1
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            nxt += a.interval
            time.sleep(max(0.0, nxt - time.time()))
    for _, label in snaps:  # interrupted early: record what exists
        snapshot(out, label + "_late")
    snapshot(out, "post")
    probe_stop[0] = True
    if probe is not None:
        probe.join(timeout=5)
    if dmon is not None:
        os.killpg(dmon.pid, signal.SIGTERM)
        dmon.wait(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
