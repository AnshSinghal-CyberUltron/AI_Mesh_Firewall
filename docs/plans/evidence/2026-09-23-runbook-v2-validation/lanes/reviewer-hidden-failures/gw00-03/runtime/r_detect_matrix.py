"""GW03 detection mis-reads, driven only through the module's own DetectHooks.

Each case builds a fake /sys/fs/cgroup + /proc tree that mirrors a common
deployment, runs gateway_v2.runtime.resources.load_contract(hooks=...), and
compares the derived plan to what the kernel would actually enforce.
"""
from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

from gateway_v2.runtime.cgroup import DetectHooks
from gateway_v2.runtime.resources import load_contract, snapshot

GiB = 1024**3
MiB = 1024**2
ENV: dict[str, str] = {}  # no overrides: pure detection path


def tree(files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp(prefix="cg-"))
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def run(name: str, files: dict[str, str], affinity: int, truth_cpu: float, truth_mem: int) -> None:
    root = tree(files)
    hooks = DetectHooks(
        cgroup_mount=str(root / "sys/fs/cgroup"),
        proc_cgroup=str(root / "proc/self/cgroup"),
        proc_meminfo=str(root / "proc/meminfo"),
        affinity=lambda: affinity,
        rlimit_nofile=lambda: (1_048_576, 1_048_576),
    )
    contract, _logs = load_contract(env=ENV, hooks=hooks)
    snap = snapshot(contract, _logs)
    truth_workers = min(math.floor(truth_cpu * 0.75), math.floor(truth_mem * 0.75 / (400 * MiB)))
    rss_total = snap["workers"] * 400 * MiB
    print(json.dumps({
        "case": name,
        "detected": {"cpu_quota": contract.cpu_quota, "cpu_source": contract.cpu_source,
                     "memory_GiB": round(contract.memory_limit / GiB, 2), "mem_source": contract.mem_source},
        "derived": {"workers": snap["workers"], "scanner_pool": snap["pools"]["scanner"],
                    "queue_depth": snap["queue_depth"], "worker_rss_budget_GiB": round(rss_total / GiB, 2)},
        "kernel_enforced": {"cpus": truth_cpu, "memory_GiB": round(truth_mem / GiB, 2),
                            "workers_if_detected_correctly": truth_workers},
        "oversubscription_x": round(snap["workers"] / max(truth_workers, 1), 1),
        "worker_rss_exceeds_memory_limit": rss_total > truth_mem,
    }))


MEMINFO_64G = "MemTotal:       67108864 kB\n"

# A. cgroup v2, limits on an ANCESTOR (systemd slice / --cgroup-parent / pod-level), leaf = max
run("v2-ancestor-limit-leaf-max",
    {"proc/self/cgroup": "0::/limited.slice/gw.service\n",
     "proc/meminfo": MEMINFO_64G,
     "sys/fs/cgroup/limited.slice/cpu.max": "200000 100000\n",
     "sys/fs/cgroup/limited.slice/memory.max": str(2 * GiB) + "\n",
     "sys/fs/cgroup/limited.slice/gw.service/cpu.max": "max 100000\n",
     "sys/fs/cgroup/limited.slice/gw.service/memory.max": "max\n"},
    affinity=16, truth_cpu=2.0, truth_mem=2 * GiB)

# B. cgroup v2, quota larger than the cpuset (docker --cpus=8 --cpuset-cpus=0,1; taskset in a --cpus=8 box)
run("v2-quota-gt-cpuset",
    {"proc/self/cgroup": "0::/\n",
     "proc/meminfo": MEMINFO_64G,
     "sys/fs/cgroup/cpu.max": "800000 100000\n",
     "sys/fs/cgroup/memory.max": str(16 * GiB) + "\n"},
    affinity=2, truth_cpu=2.0, truth_mem=16 * GiB)

# C. cgroup v1 host process (AL2 / Ubuntu 20.04 systemd unit with CPUQuota=200%, MemoryLimit=2G)
run("v1-host-unit-limit",
    {"proc/self/cgroup": "5:memory:/system.slice/gw.service\n4:cpu,cpuacct:/system.slice/gw.service\n0::/system.slice/gw.service\n",
     "proc/meminfo": MEMINFO_64G,
     "sys/fs/cgroup/cpu/cpu.cfs_quota_us": "-1\n",
     "sys/fs/cgroup/cpu/cpu.cfs_period_us": "100000\n",
     "sys/fs/cgroup/cpu/system.slice/gw.service/cpu.cfs_quota_us": "200000\n",
     "sys/fs/cgroup/cpu/system.slice/gw.service/cpu.cfs_period_us": "100000\n",
     "sys/fs/cgroup/memory/memory.limit_in_bytes": "9223372036854771712\n",
     "sys/fs/cgroup/memory/system.slice/gw.service/memory.limit_in_bytes": str(2 * GiB) + "\n"},
    affinity=16, truth_cpu=2.0, truth_mem=2 * GiB)

# Control: same limits placed on the leaf itself -> detection is correct
run("control-v2-leaf-limit",
    {"proc/self/cgroup": "0::/limited.slice/gw.service\n",
     "proc/meminfo": MEMINFO_64G,
     "sys/fs/cgroup/limited.slice/gw.service/cpu.max": "200000 100000\n",
     "sys/fs/cgroup/limited.slice/gw.service/memory.max": str(2 * GiB) + "\n"},
    affinity=16, truth_cpu=2.0, truth_mem=2 * GiB)
