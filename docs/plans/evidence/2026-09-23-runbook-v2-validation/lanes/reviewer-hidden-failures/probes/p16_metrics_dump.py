"""P16: per-worker metrics dump cost (benchmarked runtime/metrics.py).  With RV_METRICS_DIR set (as on the unit,
/dev/shm/rv-metrics), app.py runs Registry.dump() every 1.0 s ON THE EVENT LOOP; export() walks all 3,776 buckets of
every histogram (dense list) to build the sparse dict, then json.dumps + write.  Number of histograms taken from a
real worker dump of the E2E run."""
import json, random, sys, tempfile, time
sys.path.insert(0, sys.argv[1])
from rvproto.runtime.metrics import Registry
names = list(json.load(open(sys.argv[2]))["hist"].keys())
extra = ["t_guard_wait_ns", "guard_owner_rtt_ns", "guard_queue_ns", "guard_exec_ns", "dispatch_pool_wait_ns", "release_lag_ns",
         "holdback_wait_ns", "release_processing_ns", "provider_first_bytes_ns", "cancel_propagation_ns"]
reg = Registry(0)
for n in set(names + extra):
    for _ in range(20000):
        reg.observe(n, int(random.lognormvariate(14, 1.5)))
d = tempfile.mkdtemp()
xs = []
for _ in range(30):
    t0 = time.perf_counter(); reg.dump(d); xs.append(time.perf_counter() - t0)
xs.sort()
print(f"histograms={len(reg.hist)}  dump() per call: median={1e3*xs[15]:.2f} ms  max={1e3*xs[-1]:.2f} ms  "
      f"-> event-loop stall once per second per worker = {100*xs[15]:.2f}% of wall time")
