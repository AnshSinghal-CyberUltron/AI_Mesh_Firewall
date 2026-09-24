#!/usr/bin/env python3
"""VM ledger (one JSON line per VM, GCP.md): ledger.py FILE create NAME ZONE TYPE LANE [NOTE]
                                              ledger.py FILE delete NAME
Rewrites atomically under an exclusive lock so parallel helpers cannot corrupt it."""
import fcntl
import json
import os
import sys
from datetime import datetime, timezone


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    path, op = sys.argv[1], sys.argv[2]
    lock = open(path + ".lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    rows = []
    if os.path.exists(path):
        rows = [json.loads(l) for l in open(path) if l.strip()]
    if op == "create":
        name, zone, mtype, lane = sys.argv[3:7]
        note = sys.argv[7] if len(sys.argv) > 7 else ""
        rows.append({"name": name, "zone": zone, "machine_type": mtype, "lane": lane, "create_utc": now(),
                     "delete_utc": None, "note": note})
    elif op == "delete":
        name = sys.argv[3]
        hit = False
        for r in rows:
            if r["name"] == name and r.get("delete_utc") is None:
                r["delete_utc"] = now()
                hit = True
        if not hit:
            print(f"ledger: no open entry for {name}", file=sys.stderr)
    else:
        print("usage: ledger.py FILE create|delete ...", file=sys.stderr)
        return 2
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    os.replace(tmp, path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
