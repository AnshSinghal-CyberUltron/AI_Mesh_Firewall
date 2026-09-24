#!/usr/bin/env python3
"""Exact kernel TCP counters + socket-state counts (for connections-per-request). Prints one JSON line."""
import json, subprocess, time
def table(path):
    lines = open(path).read().splitlines(); out = {}
    for h, v in zip(lines[0::2], lines[1::2]):
        k = h.split(":")[0]; out[k] = dict(zip(h.split()[1:], map(int, v.split()[1:])))
    return out
snmp, nets = table("/proc/net/snmp"), table("/proc/net/netstat")
tcp = {k: snmp["Tcp"][k] for k in ("ActiveOpens", "PassiveOpens", "AttemptFails", "EstabResets", "CurrEstab", "InSegs", "OutSegs", "RetransSegs", "OutRsts")}
ext = {k: nets["TcpExt"].get(k) for k in ("TW", "TWRecycled", "TWKilled", "ListenOverflows", "ListenDrops", "TCPTimeWaitOverflow", "TCPAbortOnData", "TCPAbortOnClose")}
st = subprocess.run(["ss", "-s"], capture_output=True, text=True).stdout
states = {}
for s in ("established", "time-wait", "syn-sent", "fin-wait-1", "fin-wait-2", "close-wait", "last-ack"):
    r = subprocess.run(["ss", "-H", "-t", "-n", "state", s], capture_output=True, text=True).stdout
    states[s] = len([l for l in r.splitlines() if l.strip()])
print(json.dumps({"t": time.time(), "tcp": tcp, "tcpext": ext, "states": states, "ss_s": st.splitlines()[:3]}))
