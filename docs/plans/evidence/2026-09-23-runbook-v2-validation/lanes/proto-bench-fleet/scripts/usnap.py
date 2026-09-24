#!/usr/bin/env python3
"""Unit-side snapshot (runs ON a unit): rvproto merged metrics (/metrics/all), per-process CPU
(schedstat ns summed over all threads; accurate, unlike /proc/stat on these kernels), NIC counters,
iptables ACCT counters. Usage: usnap.py OUT.json [port]"""
import json, os, subprocess, sys, time, urllib.request
out, port = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "8400")
snap = {"t_wall": time.time(), "t_mono_ns": time.monotonic_ns()}
try:
    snap["metrics_all"] = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics/all", timeout=10).read())
except Exception as e:  # noqa: BLE001
    snap["metrics_all_error"] = repr(e)
procs = {}
for pid in os.listdir("/proc"):
    if not pid.isdigit():
        continue
    try:
        cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        continue
    if "rvproto.serve" not in cmd and "redis-server" not in cmd and "nginx" not in cmd and "synthprov" not in cmd:
        continue
    ns = 0
    try:
        for tid in os.listdir(f"/proc/{pid}/task"):
            ns += int(open(f"/proc/{pid}/task/{tid}/schedstat").read().split()[0])
    except OSError:
        continue
    role = "launcher"
    if "--worker" in cmd:
        role = "worker"
    elif "--guard-owner" in cmd:
        role = "owner"
    elif "redis-server" in cmd:
        role = "redis"
    elif "nginx" in cmd:
        role = "nginx"
    elif "synthprov" in cmd:
        role = "synthprov"
    procs[pid] = {"role": role, "cpu_ns": ns, "cmd": cmd[:120]}
snap["procs"] = procs
snap["netdev"] = open("/proc/net/dev").read()
try:
    snap["iptables_acct"] = subprocess.run(["sudo", "iptables", "-L", "ACCT", "-v", "-x", "-n"], capture_output=True,
                                           text=True, timeout=10).stdout
except Exception as e:  # noqa: BLE001
    snap["iptables_acct"] = repr(e)
snap["loadavg"] = open("/proc/loadavg").read()
snap["nproc"] = os.cpu_count()
json.dump(snap, open(out, "w"))
print(out)
