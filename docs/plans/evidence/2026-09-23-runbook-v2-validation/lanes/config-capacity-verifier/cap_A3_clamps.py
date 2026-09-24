"""Execute the REAL resource_budget detector/formula on faked hosts to show the clamps.

detect() is driven with a fake cgroup root (cgroup v2 cpu.max / memory.max files),
a fake /proc/meminfo and an injected affinity callable, so the whole detection path
(not just compute_sizing) runs. nofile is passed through compute_sizing because
detect() reads the real RLIMIT_NOFILE.
"""
import json
import os
import tempfile

from ai_mesh_shared import resource_budget as rb

GIB = 1024 ** 3


def fake_host(cpus: int, mem_gib: int) -> rb.ResourceBudget:
    root = tempfile.mkdtemp()
    cg = os.path.join(root, "cgroup")
    os.makedirs(cg)
    # cgroup v2 at mount root (Docker private cgroupns): quota = cpus * period
    with open(os.path.join(cg, "cgroup.controllers"), "w") as f:
        f.write("cpu memory\n")
    with open(os.path.join(cg, "cpu.max"), "w") as f:
        f.write(f"{cpus * 100000} 100000\n")
    with open(os.path.join(cg, "memory.max"), "w") as f:
        f.write(f"{mem_gib * GIB}\n")
    proc_cgroup = os.path.join(root, "proc_self_cgroup")
    with open(proc_cgroup, "w") as f:
        f.write("0::/\n")
    meminfo = os.path.join(root, "meminfo")
    with open(meminfo, "w") as f:
        f.write(f"MemTotal: {mem_gib * 1024 * 1024 + 1024} kB\n")
    return rb.detect(env={}, cgroup_mount=cg, proc_cgroup=proc_cgroup,
                     proc_meminfo=meminfo, affinity=lambda: cpus)


for cpus, mem in ((8, 16), (16, 64), (64, 256), (192, 1024)):
    b = fake_host(cpus, mem)
    print(f"detect() fake host {cpus} CPU / {mem} GiB -> cpu_source={b.cpu_source} mem_source={b.mem_source} "
          f"workers={b.workers} asgi_threads={b.asgi_threads} scanner_pool={b.scanner_pool} "
          f"vault_pool={b.vault_pool} redis_pool={b.redis_pool} bedrock_pool={b.bedrock_pool}")

print()
for cpus, mem, nofile in ((64, 256, 1_048_576), (192, 1024, 1_048_576)):
    b = rb.compute_sizing(float(cpus), mem * GIB, nofile=nofile)
    print(f"compute_sizing({cpus} cpu, {mem} GiB, nofile={nofile}) -> "
          + json.dumps({k: getattr(b, k) for k in ("workers", "asgi_threads", "scanner_pool", "vault_pool",
                                                    "redis_pool", "bedrock_pool", "pg_max_conns")}))
# Which env knobs does detect() honour? (clamp bounds are NOT among them)
import inspect
src = inspect.getsource(rb.detect)
print("\nenv knobs read by detect():", sorted(set(__import__('re').findall(r'_env\(env, "([A-Z_]+)"\)', src))))
