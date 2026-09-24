#!/usr/bin/env python3
"""VM ledger helper: one JSON line per VM, create/delete UTC recorded.
usage: ledger.py create <name> <zone> <machine_type> [note]
       ledger.py delete <name>
"""
import json, sys, datetime, pathlib, fcntl
LEDGER = pathlib.Path(__file__).resolve().parent.parent / "vm-ledger.jsonl"
def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def main():
    op = sys.argv[1]
    LEDGER.touch()
    with open(LEDGER, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        rows = [json.loads(l) for l in f if l.strip()]
        if op == "create":
            name, zone, mt = sys.argv[2:5]
            note = sys.argv[5] if len(sys.argv) > 5 else ""
            rows.append({"name": name, "zone": zone, "machine_type": mt, "lane": "guard",
                         "create_utc": now(), "delete_utc": None, "note": note})
        elif op == "delete":
            name = sys.argv[2]
            for r in rows:
                if r["name"] == name and r["delete_utc"] is None:
                    r["delete_utc"] = now()
        f.seek(0); f.truncate()
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(json.dumps(rows[-1] if op == "create" else [r for r in rows if r["name"] == sys.argv[2]]))
main()
