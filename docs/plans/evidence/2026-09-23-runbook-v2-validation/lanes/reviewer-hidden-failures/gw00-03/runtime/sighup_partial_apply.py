"""GatewayRuntime.apply() is not atomic: contract is swapped, some pools resized, then it raises.

Drives the real pools.GatewayRuntime.reload() with DetectHooks whose files/rlimit change
between start and reload (fd soft-limit lowered, e.g. by prlimit or a new ulimit policy).
"""
import tempfile
from pathlib import Path

from gateway_v2.runtime.cgroup import DetectHooks
from gateway_v2.runtime.errors import CapacityUnavailable
from gateway_v2.runtime.pools import GatewayRuntime
from gateway_v2.runtime.resources import load_contract
from gateway_v2.runtime.kinds import PoolKind

root = Path(tempfile.mkdtemp())
(root / "cg").mkdir()
(root / "cg/cpu.max").write_text("800000 100000\n")
(root / "cg/memory.max").write_text(str(16 * 1024**3) + "\n")
(root / "cgroup").write_text("0::/\n")
(root / "meminfo").write_text("MemTotal: 67108864 kB\n")
fd = {"soft": 65536}
hooks = DetectHooks(cgroup_mount=str(root / "cg"), proc_cgroup=str(root / "cgroup"),
                    proc_meminfo=str(root / "meminfo"), affinity=lambda: 16,
                    rlimit_nofile=lambda: (fd["soft"], fd["soft"]))
c, logs = load_contract(env={}, hooks=hooks)
rt = GatewayRuntime(c, logs, hooks=hooks, env={})
rt.pools[PoolKind.PROVIDER].acquire()  # an in-flight lease
print("before:", "contract.cpu_quota=", rt.contract.cpu_quota, "fd=", rt.contract.fd_limit,
      {k.value: p.size for k, p in rt.pools.items()}, "in_flight=", rt.in_flight())
# downscale cpu 8 -> 4 AND fd soft limit 65536 -> 4 (below workers), then reload as SIGHUP would
(root / "cg/cpu.max").write_text("400000 100000\n")
fd["soft"] = 4
try:
    rt.reload()
except CapacityUnavailable as exc:
    print("reload raised:", exc)
print("after: contract.cpu_quota=", rt.contract.cpu_quota, "fd=", rt.contract.fd_limit,
      "reload_count=", rt.reload_count,
      {k.value: p.size for k, p in rt.pools.items()})
print("=> contract says fd=4/workers=3 but redis pool still", rt.pools[PoolKind.REDIS].size,
      "; as_dict() now raises:", end=" ")
try:
    rt.as_dict()
    print("no")
except CapacityUnavailable as exc:
    print(repr(exc))
