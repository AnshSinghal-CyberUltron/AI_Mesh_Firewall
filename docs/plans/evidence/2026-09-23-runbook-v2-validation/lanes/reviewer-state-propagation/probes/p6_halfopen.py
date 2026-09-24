"""P6: store failover that leaves the OLD connections half-open (peer gone, no RST: the classic
managed-Redis failover / VM loss). New connections to the store work immediately.
The background pool (kill-switch refresher, plan reconcile, audit writer) has no socket timeout
and no keepalive; the request pool has socket_timeout = target_p99_ms.
  python p6_halfopen.py <observe_seconds>
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request

from pc import KEY_B, log, metrics_all, outcome, probe, readyz, sweep

CTL = "http://127.0.0.1:26390"


def ctl(path: str) -> dict:
    return json.loads(urllib.request.urlopen(CTL + path, timeout=5).read())


def ages() -> list:
    return [(w["worker"], round(w["gauge"].get("killswitch_snapshot_age_seconds", -1), 1),
             round(w["gauge"].get("plan_snapshot_age_seconds", -1), 1)) for w in metrics_all()["per_worker"]]


def main() -> None:
    observe = float(sys.argv[1])
    ctl("/mode?m=pass")
    log("H0 healthy", sweep=sweep(KEY_B, 8), proxy=ctl("/stats"))
    ctl("/blackhole_existing")
    t0 = time.time()
    log("H1 every EXISTING store connection is now half-open; NEW connections reach the healthy store")
    while time.time() - t0 < observe:
        time.sleep(5)
        rz = readyz()
        log(f"H2 +{round(time.time() - t0)}s", sweep=sweep(KEY_B, 8),
            readyz={"status": rz["status"], "killswitch": rz["killswitch"], "plans_fresh": rz["plans_fresh"]},
            ks_and_plan_age_per_worker=ages(),
            proxy={k: v for k, v in ctl("/stats").items() if k in ("live_conns", "blackholed_conns", "accepted")})
    ctl("/reset_existing")  # only an RST on the hung sockets lets the refreshers resume
    t1 = time.time()
    time.sleep(1.5)
    log("H3 after RST of the half-open sockets (+1.5 s)", sweep=sweep(KEY_B, 8), ks_and_plan_age_per_worker=ages())


if __name__ == "__main__":
    main()
