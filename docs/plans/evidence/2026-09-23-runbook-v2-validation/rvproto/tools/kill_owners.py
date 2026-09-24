"""SIGKILL every guard-owner process of this host's rvproto (pids from <socket dir>/*.ready).
Usage (on the unit): RV_GUARD_SOCKET_DIR=~/rv/run/guard python tools/kill_owners.py"""

import glob
import json
import os
import signal

d = os.path.expanduser(os.environ.get("RV_GUARD_SOCKET_DIR", "~/rv/run/guard"))
killed = []
for p in sorted(glob.glob(os.path.join(d, "guard-*.sock.ready"))):
    pid = json.load(open(p))["pid"]
    os.kill(pid, signal.SIGKILL)
    killed.append(pid)
print(json.dumps({"killed": killed}))
