"""Execute gateway_v2 ResourceContract at HEAD for several CPU quotas (no docker)."""
import json, sys
from gateway_v2.runtime.resources import from_signals, snapshot
from gateway_v2.runtime.kinds import HardwareSignals
import dataclasses
fields = [f.name for f in dataclasses.fields(HardwareSignals)]
out = {"HardwareSignals_fields": fields, "runs": {}}
for cpu in (1.0, 1.5, 2.0, 4.0, 8.0, 16.0):
    kw = dict(cpu_quota=cpu, memory_limit=64 * 1024**3, fd_limit=65536,
              cpu_source="probe", mem_source="probe", fd_source="probe")
    kw = {k: v for k, v in kw.items() if k in fields}
    sig = HardwareSignals(**kw)
    try:
        c = from_signals(sig, target_p99_ms=20.0, utilization_cap=0.75, per_worker_rss=400 * 1024 * 1024)
        s = snapshot(c, ())
        rate = c.offered_service_rate()
        out["runs"][str(cpu)] = {"workers": s["workers"], "queue_depth": s["queue_depth"],
            "pools": s["pools"], "offered_service_rate_rps": rate,
            "implied_rps_per_vcpu": rate / cpu}
    except Exception as exc:
        out["runs"][str(cpu)] = {"error": f"{type(exc).__name__}: {exc}"}
json.dump(out, sys.stdout, indent=2)
