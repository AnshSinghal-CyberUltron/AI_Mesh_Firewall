"""install_sighup() runs runtime.reload() -> DummyPool.resize() INSIDE the signal handler.

Python runs signal handlers on the main thread between bytecodes. DummyPool guards state with a
non-reentrant threading.Lock (via Condition). If SIGHUP lands while the main thread is inside
acquire()/release() (i.e. holds that lock), resize() blocks on the lock its own thread holds:
the main thread deadlocks forever. This harness uses only the public GW03 API.
"""
import faulthandler
import os
import signal
import sys
import tempfile
import threading
import time
from pathlib import Path

from gateway_v2.runtime.cgroup import DetectHooks
from gateway_v2.runtime.kinds import PoolKind
from gateway_v2.runtime.lifecycle import install_sighup
from gateway_v2.runtime.pools import GatewayRuntime
from gateway_v2.runtime.resources import load_contract

root = Path(tempfile.mkdtemp())
(root / "cpu.max").write_text("400000 100000\n")
(root / "memory.max").write_text(str(8 * 1024**3) + "\n")
(root / "cgroup").write_text("0::/\n")
(root / "meminfo").write_text("MemTotal: 67108864 kB\n")
hooks = DetectHooks(cgroup_mount=str(root), proc_cgroup=str(root / "cgroup"),
                    proc_meminfo=str(root / "meminfo"), affinity=lambda: 16,
                    rlimit_nofile=lambda: (65536, 65536))
c, logs = load_contract(env={}, hooks=hooks)
rt = GatewayRuntime(c, logs, hooks=hooks, env={})
install_sighup(rt)
pool = rt.pools[PoolKind.SCANNER]
counter = {"ops": 0, "hups": 0}
main_pid = os.getpid()


def sender() -> None:
    while True:
        os.kill(main_pid, signal.SIGHUP)
        counter["hups"] += 1
        time.sleep(0.003)


def watchdog() -> None:
    last, still = -1, 0
    while True:
        time.sleep(0.5)
        if counter["ops"] == last:
            still += 1
            if still >= 6:  # 3 s without a single acquire/release on the main thread
                print(f"DEADLOCK: main thread stuck after ops={last} hups={counter['hups']} "
                      f"reloads={rt.reload_count}", flush=True)
                faulthandler.dump_traceback(file=sys.stdout, all_threads=True)
                os._exit(3)
        else:
            last, still = counter["ops"], 0


threading.Thread(target=sender, daemon=True).start()
threading.Thread(target=watchdog, daemon=True).start()
t_end = time.monotonic() + 60
while time.monotonic() < t_end:
    pool.acquire()   # request path on the main (event-loop) thread
    pool.release()
    counter["ops"] += 1
print(f"NO DEADLOCK in 60s ops={counter['ops']} hups={counter['hups']} reloads={rt.reload_count}")
