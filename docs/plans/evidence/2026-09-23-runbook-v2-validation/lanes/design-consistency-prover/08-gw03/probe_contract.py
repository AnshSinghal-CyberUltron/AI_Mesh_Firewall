"""Exercise the REAL GW03 ResourceContract to expose assumptions hidden in 'derived' bounds."""
from gateway_v2.runtime.resources import load_contract, snapshot
from gateway_v2.runtime.kinds import PoolKind, CapacityHint
from gateway_v2.runtime.errors import CapacityUnavailable
GiB = 1024**3
def snap(cpu, mem_gib, fd, **extra):
    env = {"AMF_CPU_QUOTA": str(cpu), "AMF_MEMORY_LIMIT_BYTES": str(int(mem_gib * GiB)), "AMF_FD_LIMIT": str(fd), **extra}
    try:
        c, logs = load_contract(env=env)
        s = snapshot(c, logs)
        return {k: s[k] for k in ("workers", "binding", "queue_depth", "connection_budget", "stream_buffer_bytes")} | {"pools": s["pools"]}
    except CapacityUnavailable as e:
        return {"REFUSE_TO_START": str(e)}
print("1) 1 vCPU / 1.3 vCPU containers with default utilization 0.75:")
for cpu in (1, 1.3): print(f"   cpu={cpu}: {snap(cpu, 4, 65536)}")
print("2) queue_depth vs declared target p99 (should change if the SLO is an input):")
for p99 in ("20", "200", "2000"): print(f"   AMF_TARGET_P99_MS={p99}: queue_depth={snap(8, 16, 65536, AMF_TARGET_P99_MS=p99)['queue_depth']}")
print("3) provider pool == workers (one upstream connection per worker, for an async multiplexing gateway):")
for cpu in (2, 4, 8, 16): s = snap(cpu, 64, 65536); print(f"   cpu={cpu}: workers={s['workers']} provider_pool={s['pools']['provider']} scanner={s['pools']['scanner']} vault={s['pools']['vault']}")
print("4) redis pool == floor((fd - workers) * 0.75):")
for fd in (1024, 65536, 1048576): print(f"   fd={fd}: redis_pool={snap(8, 16, fd)['pools']['redis']}")
print("5) stream buffer = 0.75*memory/active_streams, independent of worker RSS already budgeted from the same 0.75*memory:")
from gateway_v2.runtime.resources import from_signals
from gateway_v2.runtime.kinds import HardwareSignals
c = from_signals(HardwareSignals(16.0, "t", 4 * GiB, "t", 65536, "t"), target_p99_ms=20.0, utilization_cap=0.75, per_worker_rss=400 * 1024**2)
w = c.workers(); rss_total = w * c.per_worker_rss; buf1 = c.stream_buffer_bytes(1)
print(f"   4 GiB, 16 cpu: workers={w} (binding={c.binding_signal()}), workers*RSS={rss_total/GiB:.2f} GiB + stream_buffer_bytes(1)={buf1/GiB:.2f} GiB"
      f" = {(rss_total+buf1)/GiB:.2f} GiB vs cap 0.75*4 = 3.00 GiB")
print("6) per-worker RSS default (400 MiB, not measured) decides workers whenever RAM binds:")
for rss in ("200", "400", "800"): print(f"   AMF_PER_WORKER_RSS_MB={rss}: workers={snap(16, 4, 65536, AMF_PER_WORKER_RSS_MB=rss)['workers']}")
print("7) 'service rate' is derived from the SLO, not measured: offered_service_rate = workers / p99_s ->",
      f"{c.offered_service_rate():.0f} req/s at 7 workers & 20 ms regardless of guard/provider speed")
print("8) guard pool = tokens_per_second * p99_seconds (unit: tokens, used as a concurrency limit):",
      from_signals(HardwareSignals(8.0, "t", 16 * GiB, "t", 65536, "t"), target_p99_ms=20.0, utilization_cap=0.75,
                   per_worker_rss=400 * 1024**2, guard_capacity=CapacityHint(tokens_per_second=50_000.0)).pool_size(PoolKind.GUARD))
