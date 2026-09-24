#!/usr/bin/env python3
"""SUT resource sampler (stdlib only). Runs on the SUT host, NOT in a container.

Every --interval seconds writes one JSONL line:
  {"wall": <unix s>, "mono": <s>, "host": {cpu jiffies}, "ctr": {name: {usage_usec, user_usec, system_usec,
   nr_throttled, throttled_usec, mem_current, anon, file}}, "gw_workers": [{pid, rss_kb, cpu_ticks}], "probe": {...}}
CPU is read from cgroup v2 cpu.stat (usage_usec), never docker stats. The gateway worker list comes from the
gateway container's cgroup.procs. An optional /health probe (--probe-hz) records event-loop responsiveness.
"""
import argparse, http.client, json, os, subprocess, sys, time, threading

def ctr_ids(prefix):
    out = subprocess.run(["docker", "ps", "--no-trunc", "--format", "{{.ID}} {{.Names}}"],
                         capture_output=True, text=True, check=True).stdout
    ids = {}
    for line in out.splitlines():
        cid, name = line.split(None, 1)
        if name.startswith(prefix):
            ids[name] = cid
    return ids

def cg_path(cid):
    for p in (f"/sys/fs/cgroup/system.slice/docker-{cid}.scope", f"/sys/fs/cgroup/docker/{cid}"):
        if os.path.isdir(p):
            return p
    raise FileNotFoundError(cid)

def kv(path):
    d = {}
    with open(path) as f:
        for line in f:
            k, v = line.split()
            d[k] = int(v)
    return d

def read_ctr(p):
    c = kv(p + "/cpu.stat")
    m = kv(p + "/memory.stat")
    with open(p + "/memory.current") as f:
        cur = int(f.read())
    return {"usage_usec": c["usage_usec"], "user_usec": c["user_usec"], "system_usec": c["system_usec"],
            "nr_throttled": c.get("nr_throttled", 0), "throttled_usec": c.get("throttled_usec", 0),
            "mem_current": cur, "anon": m.get("anon", 0), "file": m.get("file", 0)}

def host_cpu():
    with open("/proc/stat") as f:
        parts = f.readline().split()[1:]
    return [int(x) for x in parts]

def workers(p):
    out = []
    try:
        with open(p + "/cgroup.procs") as f:
            pids = [int(x) for x in f.read().split()]
    except OSError:
        return out
    for pid in pids:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode(errors="replace")
            if "gunicorn" not in cmd:
                continue
            rss = 0
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        rss = int(line.split()[1])
            with open(f"/proc/{pid}/stat") as f:
                st = f.read().rsplit(")", 1)[1].split()
            out.append({"pid": pid, "rss_kb": rss, "utime": int(st[11]), "stime": int(st[12]),
                        "threads": int(st[17])})
        except OSError:
            continue
    return out

class Probe(threading.Thread):
    """GET /health at a fixed rate on a fresh connection each time; records latency (event-loop symptom)."""
    def __init__(self, host, port, hz, out_path):
        super().__init__(daemon=True)
        self.host, self.port, self.dt, self.out_path = host, port, 1.0 / hz, out_path
    def run(self):
        nxt = time.monotonic()
        with open(self.out_path, "a", buffering=1) as fh:
            while True:
                nxt += self.dt
                t0 = time.perf_counter(); status = -1; err = ""
                try:
                    c = http.client.HTTPConnection(self.host, self.port, timeout=5)
                    c.request("GET", "/health"); r = c.getresponse(); r.read(); status = r.status; c.close()
                except Exception as e:  # noqa: BLE001
                    err = type(e).__name__
                ms = (time.perf_counter() - t0) * 1000
                fh.write(json.dumps({"wall": time.time(), "ms": round(ms, 3), "status": status, "err": err}) + "\n")
                time.sleep(max(0.0, nxt - time.monotonic()))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--prefix", default="aimeshperf-")
    ap.add_argument("--gateway", default="aimeshperf-gateway-1")
    ap.add_argument("--probe-hz", type=float, default=5.0)
    ap.add_argument("--probe-out", default="")
    a = ap.parse_args()
    ids = ctr_ids(a.prefix)
    paths = {n: cg_path(c) for n, c in ids.items()}
    if a.probe_hz > 0 and a.probe_out:
        Probe("127.0.0.1", 8300, a.probe_hz, a.probe_out).start()
    nxt = time.monotonic()
    with open(a.out, "a", buffering=1) as fh:
        fh.write(json.dumps({"meta": {"containers": ids, "cgroups": paths, "clk_tck": os.sysconf("SC_CLK_TCK"),
                                      "nproc": os.cpu_count()}}) + "\n")
        while True:
            rec = {"wall": time.time(), "mono": time.monotonic(), "host": host_cpu(), "ctr": {}}
            for n, p in paths.items():
                try:
                    rec["ctr"][n] = read_ctr(p)
                except OSError:
                    rec["ctr"][n] = None
            rec["gw_workers"] = workers(paths.get(a.gateway, ""))
            fh.write(json.dumps(rec) + "\n")
            nxt += a.interval
            time.sleep(max(0.0, nxt - time.monotonic()))

if __name__ == "__main__":
    main()
