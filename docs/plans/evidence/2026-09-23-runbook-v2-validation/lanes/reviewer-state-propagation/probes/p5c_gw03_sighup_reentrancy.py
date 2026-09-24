"""P5c: gateway_v2 GW03 SIGHUP reload (repo code, read-only import) while the MAIN thread is using
a pool (the thread a Python signal handler runs on). install_sighup() uses signal.signal(), so the
handler runs between two bytecodes of whatever the main thread is doing; DummyPool.resize() takes
the same non-reentrant threading.Lock that acquire()/release() hold.
  PYTHONPATH=<repo>/gateway_v2 python p5c_gw03_sighup_reentrancy.py <seconds>
"""

from __future__ import annotations

import faulthandler
import json
import os
import signal
import subprocess
import sys
import threading
import time

from gateway_v2.runtime.kinds import PoolKind
from gateway_v2.runtime.lifecycle import install_sighup
from gateway_v2.runtime.pools import GatewayRuntime
from gateway_v2.runtime.resources import load_contract

ENV = {"AMF_CPU_QUOTA": "4", "AMF_MEMORY_LIMIT_BYTES": str(64 * 400 * 1024 * 1024), "AMF_FD_LIMIT": "65536",
       "AMF_PER_WORKER_RSS_MB": "400", "AMF_UTILIZATION_CAP": "0.75", "AMF_TARGET_P99_MS": "20"}


def main() -> None:
    seconds = float(sys.argv[1])
    contract, logs = load_contract(env=dict(ENV))
    rt = GatewayRuntime(contract, logs, env=dict(ENV))
    install_sighup(rt)
    pool = rt.pools[PoolKind.SCANNER]
    progress = [0]
    stuck = threading.Event()

    def watchdog() -> None:
        last, since = -1, time.monotonic()
        while True:
            time.sleep(0.2)
            if progress[0] != last:
                last, since = progress[0], time.monotonic()
            elif time.monotonic() - since > 3.0:
                print(json.dumps({"result": "DEADLOCK", "main_thread_progress": progress[0],
                                  "reloads_completed": rt.reload_count, "pool_held": pool.held,
                                  "note": "main thread blocked >3 s inside the SIGHUP handler; stack follows"}), flush=True)
                faulthandler.dump_traceback(all_threads=True)
                stuck.set()
                os._exit(3)

    threading.Thread(target=watchdog, daemon=True).start()
    sender = subprocess.Popen([sys.executable, "-c",
                               "import os,signal,sys,time\n"
                               f"p={os.getpid()}\n"
                               f"end=time.time()+{seconds}\n"
                               "while time.time()<end:\n"
                               "    os.kill(p, signal.SIGHUP); time.sleep(float(os.environ.get(\"HUP_GAP\", \"0.002\")))\n"])
    t0 = time.time()
    while time.time() - t0 < seconds:  # the request path using the pool on the main thread
        pool.acquire()
        pool.release()
        progress[0] += 1
    sender.wait()
    print(json.dumps({"result": "no deadlock", "acquire_release_cycles": progress[0],
                      "reloads_completed": rt.reload_count}), flush=True)


if __name__ == "__main__":
    main()
