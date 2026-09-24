"""P1 on the LIVE 4-worker rvproto (:8493):
  miss   - plan change with NO notification (dropped pub/sub): convergence via reconcile only
  flush  - store flush while serving, control plane re-seeds identical versions -> wedge
  (python p1_server.py miss|flush)
"""

from __future__ import annotations

import json
import subprocess
import sys
import time

from pc import KEY_A, KEY_B, log, r, readyz, sweep
from pc import probe, outcome

SP = "/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad"
PY = f"{SP}/rvproto/.venv/bin/python"
SEED = [PY, f"{SP}/evidence/reviewer-state-propagation/rvproto-frozen1/tools/seed.py", "redis://127.0.0.1:26379/0"]


def miss() -> None:
    rr = r()
    doc = json.loads(rr.get("rv:plan:org-b"))
    doc["version"] = "b-nopush-2"
    p = rr.pipeline()
    p.set("rv:plan:org-b", json.dumps(doc))
    p.hset("rv:plan_versions", "org-b", "b-nopush-2")  # NO publish: the notification is lost
    p.execute()
    t0 = time.time()
    log("miss0 org-b changed in the store without a notification")
    while time.time() - t0 < 6:
        res = [outcome(probe(KEY_B)) for _ in range(12)]
        if all("b-nopush-2" in x for x in res):
            log("miss1 every probe on the new version", converged_after_s=round(time.time() - t0, 3))
            return
    log("miss1 NOT converged in 6 s", last=res)


def flush() -> None:
    rr = r()
    log("F0 baseline", org_a=sweep(KEY_A, 12), org_b=sweep(KEY_B, 12), readyz=readyz())
    rr.flushdb()
    time.sleep(2.5)
    log("F1 store flushed (restart w/o persistence / empty replica) +2.5 s",
        org_a=sweep(KEY_A, 12), org_b=sweep(KEY_B, 12), readyz=readyz())
    out = subprocess.run(SEED, capture_output=True, text=True, check=True).stdout.strip()
    log("F2 control plane re-seeds keys, budgets, kill-switch and the SAME compiled plan versions", seed=out,
        store_versions={k.decode(): v.decode() for k, v in rr.hgetall("rv:plan_versions").items()})
    for i in range(1, 7):
        time.sleep(5)
        log(f"F3 +{5 * i}s after re-seed", org_a=sweep(KEY_A, 12), org_b=sweep(KEY_B, 12), readyz=readyz())
    doc = json.loads(rr.get("rv:plan:org-a"))
    doc["version"] = "a-2"
    p = rr.pipeline()
    p.set("rv:plan:org-a", json.dumps(doc))
    p.hset("rv:plan_versions", "org-a", "a-2")
    p.publish("rv:plan:updates", "org-a")
    p.execute()
    time.sleep(2)
    log("F4 only a VERSION CHANGE revives a tenant (org-a -> a-2); org-b still wedged",
        org_a=sweep(KEY_A, 12), org_b=sweep(KEY_B, 12))


if __name__ == "__main__":
    {"miss": miss, "flush": flush}[sys.argv[1]]()
