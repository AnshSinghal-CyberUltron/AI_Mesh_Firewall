#!/usr/bin/env python3
"""Timed shared-store writes for the multi-unit correctness tests (runs on a loadgen so the write
instant is on the same clock as that loadgen's request records). Stdlib only (raw RESP).

  redis_timed.py HOST:PORT plan ORG VERSION PLAN_JSON_FILE   # SET plan, HSET versions, PUBLISH (= tools/plan_update.py)
  redis_timed.py HOST:PORT ks-org ORG on|off                 # HSET rv:killswitch:org (= tools/killswitch.py org)
  redis_timed.py HOST:PORT budget ORG TOKENS                  # SET rv:budget:ORG (= tools/quota.py set)
  redis_timed.py HOST:PORT get KEY
Prints JSON: t_send (unix s, just before the pipeline is written), t_reply (unix s, after the last reply),
replies. The write takes effect at the server between t_send and t_reply."""
import json, socket, sys, time

def enc(*args):
    out = b"*%d\r\n" % len(args)
    for a in args:
        b = a if isinstance(a, bytes) else str(a).encode()
        out += b"$%d\r\n%s\r\n" % (len(b), b)
    return out

def read_reply(f):
    line = f.readline()
    t = line[:1]
    if t in (b"+", b"-", b":"):
        return line[1:].strip().decode()
    if t == b"$":
        n = int(line[1:])
        if n < 0:
            return None
        data = f.read(n + 2)
        return data[:-2].decode(errors="replace")
    if t == b"*":
        return [read_reply(f) for _ in range(int(line[1:]))]
    raise RuntimeError(line)

host, port = sys.argv[1].rsplit(":", 1)
op = sys.argv[2]
cmds = []
if op == "plan":
    org, ver, path = sys.argv[3:6]
    doc = open(path).read().strip()
    cmds = [("SET", "rv:plan:" + org, doc), ("HSET", "rv:plan_versions", org, ver), ("PUBLISH", "rv:plan:updates", org)]
elif op == "ks-org":
    org, state = sys.argv[3:5]
    cmds = [("HSET", "rv:killswitch:org", org, "1" if state == "on" else "0")]
elif op == "budget":
    org, tokens = sys.argv[3:5]
    cmds = [("SET", "rv:budget:" + org, str(int(tokens)))]
elif op == "get":
    cmds = [("GET", sys.argv[3])]
s = socket.create_connection((host, int(port)), timeout=5)
s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
f = s.makefile("rb")
payload = b"".join(enc(*c) for c in cmds)
t_send = time.time()
s.sendall(payload)
replies = [read_reply(f) for _ in cmds]
t_reply = time.time()
print(json.dumps({"op": op, "args": sys.argv[3:], "t_send": t_send, "t_reply": t_reply,
                  "rtt_ms": round((t_reply - t_send) * 1000, 3), "replies": replies}))
