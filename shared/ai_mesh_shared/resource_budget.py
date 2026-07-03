"""Container/cgroup-aware resource budget detector and worker-sizing formula.

The AI Mesh Firewall backend (gateway = gunicorn/uvicorn, control = Django ASGI)
must use the *full* machine it is given — one async worker per core, thread pools
sized to cores, DB/pool connections scaled to workers — while treating RAM as a
**ceiling with headroom** so it never OOM-kills. All sizing is derived here at
process start; there are no hardcoded worker/thread counts in the runtime path.

Detection order (most specific wins):
  1. cgroup v2  — ``cpu.max`` (quota/period) and ``memory.max`` at the process's
     own cgroup (``/proc/self/cgroup``) and at the mount root (Docker with a
     private cgroupns exposes the container's cgroup as root).
  2. cgroup v1  — ``cpu/cpu.cfs_quota_us`` + ``cpu.cfs_period_us`` and
     ``memory/memory.limit_in_bytes``.
  3. Fallback   — ``os.sched_getaffinity(0)`` for CPUs and ``/proc/meminfo``
     ``MemTotal`` for RAM (bare-metal / unconstrained host).

CPU and RAM are each the *minimum* of every signal that applies: a cgroup quota
caps total CPU-time, an affinity mask caps which cores are runnable, and physical
RAM caps a bogus (``max`` / near-int64) cgroup memory limit. Taking the min keeps
the budget honest under every combination.

This module is intentionally dependency-free (stdlib only) so it can be imported
from gateway, control, and workers without pulling Django/FastAPI. See
``shared/README.md``.

CLI (used by service entrypoints)::

    python -m ai_mesh_shared.resource_budget --json          # full report
    python -m ai_mesh_shared.resource_budget --export        # KEY=VALUE for eval
    python -m ai_mesh_shared.resource_budget --value workers # one number
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass
from typing import Callable, Iterable, Optional

# ---------------------------------------------------------------------------
# Defaults (all overridable via env or explicit args)
# ---------------------------------------------------------------------------

#: Fraction of detected RAM we allow the worker pool to occupy. The remainder is
#: headroom for the kernel page cache, DB/Redis clients, burst allocations, and
#: the other co-tenant services on the box. Never 1.0 — that OOM-kills.
DEFAULT_HEADROOM = 0.75

#: Conservative per-worker resident set size used as the RAM divisor. Refined by
#: an empirical measurement (perf_scratchpad item 02) via PER_WORKER_RSS_MB.
DEFAULT_PER_WORKER_RSS_MB = 512

#: Worker count floor. Even a 1-core box keeps 2 workers so a single slow/blocked
#: request cannot wedge the whole service (availability > strict 1:1 on tiny boxes).
DEFAULT_MIN_WORKERS = 2

#: ASGI/asgiref thread-pool clamps (threads that run sync work off the event loop).
DEFAULT_THREAD_LO = 8
DEFAULT_THREAD_HI = 32

#: Extra Postgres connections beyond ``workers * (db_threads + 1)`` to cover celery,
#: admin/psql sessions, migrations, and co-tenant harnesses sharing the same DB.
DEFAULT_PG_MARGIN = 50

_BYTES_PER_MB = 1024 * 1024
_BYTES_PER_GIB = 1024 * 1024 * 1024
_CGROUP_MOUNT = "/sys/fs/cgroup"
#: A cgroup memory limit at/above this is "unlimited" (kernel writes ~int64 max).
_UNLIMITED_THRESHOLD = 1 << 62


# ---------------------------------------------------------------------------
# Small IO helpers (injectable root for tests)
# ---------------------------------------------------------------------------

def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r") as fh:
            return fh.read().strip()
    except (OSError, ValueError):
        return None


def _cgroup_v2_relpath(proc_cgroup: str) -> str:
    """Relative cgroup path from a v2 ``/proc/self/cgroup`` line (``0::/path``)."""
    text = _read_text(proc_cgroup) or ""
    for line in text.splitlines():
        # v2 unified hierarchy is the ``0::`` entry.
        if line.startswith("0::"):
            return line.split("::", 1)[1].strip()
    return ""


def _cgroup_v2_bases(mount: str, proc_cgroup: str) -> Iterable[str]:
    """Candidate cgroup-v2 directories, most specific first.

    Under a private cgroupns (Docker default) the container's own cgroup is
    mounted at ``mount`` root, so the relpath *and* the bare mount both resolve;
    under a host cgroupns only ``mount + relpath`` is correct. We try both.
    """
    seen = set()
    rel = _cgroup_v2_relpath(proc_cgroup).lstrip("/")
    # Walk from the process cgroup up toward the root so a limit set on a parent
    # slice (common with systemd) is still found.
    parts = rel.split("/") if rel else []
    while True:
        candidate = os.path.join(mount, *parts) if parts else mount
        if candidate not in seen:
            seen.add(candidate)
            yield candidate
        if not parts:
            break
        parts = parts[:-1]


# ---------------------------------------------------------------------------
# CPU detection
# ---------------------------------------------------------------------------

def _cgroup_v2_cpu(mount: str, proc_cgroup: str) -> Optional[float]:
    for base in _cgroup_v2_bases(mount, proc_cgroup):
        raw = _read_text(os.path.join(base, "cpu.max"))
        if not raw:
            continue
        parts = raw.split()
        if not parts or parts[0] == "max":
            # Explicit "no quota" at this level — keep walking to a parent that
            # might impose one; if none do, cgroup is not the binding signal.
            continue
        try:
            quota = int(parts[0])
            period = int(parts[1]) if len(parts) > 1 else 100000
        except ValueError:
            continue
        if quota > 0 and period > 0:
            return quota / period
    return None


def _cgroup_v1_cpu(mount: str) -> Optional[float]:
    quota = _read_text(os.path.join(mount, "cpu", "cpu.cfs_quota_us"))
    period = _read_text(os.path.join(mount, "cpu", "cpu.cfs_period_us"))
    if quota is None or period is None:
        return None
    try:
        q = int(quota)
        p = int(period)
    except ValueError:
        return None
    if q > 0 and p > 0:
        return q / p
    return None  # quota == -1 → unconstrained


def _affinity_cpus(affinity: Optional[Callable[[], int]]) -> int:
    if affinity is not None:
        return max(1, affinity())
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except AttributeError:  # pragma: no cover - non-Linux
        return max(1, os.cpu_count() or 1)


# ---------------------------------------------------------------------------
# Memory detection
# ---------------------------------------------------------------------------

def _cgroup_v2_mem(mount: str, proc_cgroup: str) -> Optional[int]:
    for base in _cgroup_v2_bases(mount, proc_cgroup):
        raw = _read_text(os.path.join(base, "memory.max"))
        if not raw:
            continue
        if raw == "max":
            continue
        try:
            val = int(raw)
        except ValueError:
            continue
        if 0 < val < _UNLIMITED_THRESHOLD:
            return val
    return None


def _cgroup_v1_mem(mount: str) -> Optional[int]:
    raw = _read_text(os.path.join(mount, "memory", "memory.limit_in_bytes"))
    if raw is None:
        return None
    try:
        val = int(raw)
    except ValueError:
        return None
    if 0 < val < _UNLIMITED_THRESHOLD:
        return val
    return None


def _meminfo_total_bytes(meminfo: str) -> Optional[int]:
    text = _read_text(meminfo)
    if not text:
        return None
    for line in text.splitlines():
        if line.startswith("MemTotal:"):
            try:
                kb = int(line.split()[1])
            except (IndexError, ValueError):
                return None
            return kb * 1024
    return None


# ---------------------------------------------------------------------------
# Formula
# ---------------------------------------------------------------------------

def _clamp(x: int, lo: int, hi: int) -> int:
    """Clamp with the lower bound taking precedence when lo > hi.

    (A 1-core box has hi=1 but we still want the floor of 2 — availability.)
    """
    return max(lo, min(x, max(lo, hi)))


@dataclass(frozen=True)
class ResourceBudget:
    """Detected hardware budget plus the derived sizing for the async stack."""

    # --- detected ---
    cpu_budget: float           # effective CPUs (may be fractional from a quota)
    ram_bytes: int              # effective RAM ceiling in bytes
    cpu_source: str             # cgroup-v2 | cgroup-v1 | affinity
    mem_source: str             # cgroup-v2 | cgroup-v1 | meminfo
    per_worker_rss: int         # RAM divisor used, bytes

    # --- derived ---
    workers: int                # async worker processes (~1 per core, RAM-bounded)
    asgi_threads: int           # sync-offload thread pool size per worker
    db_threads: int             # DB-touching threads per worker (for pg sizing)
    pg_max_conns: int           # recommended Postgres max_connections for this svc
    # gateway per-worker offload pools (each multiplies by `workers`, so clamped)
    scanner_pool: int           # Tier-1 CPU scan pool  (~cpu, bounded)
    bedrock_pool: int           # Tier-2 Bedrock network-I/O pool (== asgi_threads)
    vault_pool: int             # embedding-vault DB conn pool max (kept small — feeds pg)
    headroom: float

    @property
    def ram_gib(self) -> float:
        return self.ram_bytes / _BYTES_PER_GIB

    @property
    def ram_workers(self) -> int:
        """How many workers RAM alone would permit (diagnostic)."""
        if self.per_worker_rss <= 0:
            return self.workers
        return int((self.ram_bytes * self.headroom) // self.per_worker_rss)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["ram_gib"] = round(self.ram_gib, 2)
        d["ram_workers"] = self.ram_workers
        d["cpu_bound"] = self.ram_workers >= round(self.cpu_budget)
        return d


def compute_sizing(
    cpu_budget: float,
    ram_bytes: int,
    *,
    per_worker_rss: int = DEFAULT_PER_WORKER_RSS_MB * _BYTES_PER_MB,
    headroom: float = DEFAULT_HEADROOM,
    cpu_source: str = "given",
    mem_source: str = "given",
    min_workers: int = DEFAULT_MIN_WORKERS,
    thread_lo: int = DEFAULT_THREAD_LO,
    thread_hi: int = DEFAULT_THREAD_HI,
    db_threads: Optional[int] = None,
    pg_margin: int = DEFAULT_PG_MARGIN,
) -> ResourceBudget:
    """Apply the sizing formula to a (cpu, ram) budget.

    workers      = clamp(min(round(cpu), floor(ram*headroom / rss)), min_workers, round(cpu))
    asgi_threads = clamp(round(cpu*2), thread_lo, thread_hi)
    pg_max_conns = max(100, workers * (db_threads + 1) + margin)
    """
    cpu_workers = max(1, round(cpu_budget))
    if per_worker_rss > 0:
        ram_workers = int((ram_bytes * headroom) // per_worker_rss)
    else:
        ram_workers = cpu_workers
    workers = _clamp(min(cpu_workers, ram_workers), min_workers, cpu_workers)

    asgi_threads = _clamp(round(cpu_budget * 2), thread_lo, thread_hi)
    if db_threads is None:
        db_threads = asgi_threads
    pg_max_conns = max(100, workers * (db_threads + 1) + pg_margin)

    # Gateway per-worker offload pools. These multiply by `workers`, so each is
    # clamped to keep total threads/connections bounded (perf_scratchpad item 11):
    #   scanner (CPU-bound Tier-1) ~ one lane per core, capped at 16;
    #   bedrock (network-I/O Tier-2) == asgi_threads (threads mostly wait on the API);
    #   vault (Postgres conn pool) kept small — workers*vault_pool feeds pg_max_conns.
    scanner_pool = _clamp(round(cpu_budget), 4, 16)
    bedrock_pool = asgi_threads
    vault_pool = _clamp(round(cpu_budget / 2), 2, 8)

    return ResourceBudget(
        cpu_budget=cpu_budget,
        ram_bytes=ram_bytes,
        cpu_source=cpu_source,
        mem_source=mem_source,
        per_worker_rss=per_worker_rss,
        workers=workers,
        asgi_threads=asgi_threads,
        db_threads=db_threads,
        pg_max_conns=pg_max_conns,
        scanner_pool=scanner_pool,
        bedrock_pool=bedrock_pool,
        vault_pool=vault_pool,
        headroom=headroom,
    )


# ---------------------------------------------------------------------------
# Top-level detection
# ---------------------------------------------------------------------------

def _env(env: Optional[dict], key: str) -> Optional[str]:
    src = os.environ if env is None else env
    val = src.get(key)
    if val is None:
        return None
    val = val.strip()
    return val or None


def detect(
    *,
    env: Optional[dict] = None,
    cgroup_mount: str = _CGROUP_MOUNT,
    proc_cgroup: str = "/proc/self/cgroup",
    proc_meminfo: str = "/proc/meminfo",
    affinity: Optional[Callable[[], int]] = None,
) -> ResourceBudget:
    """Detect the machine budget and compute sizing.

    Env overrides (all optional): ``RESOURCE_BUDGET_CPUS``, ``RESOURCE_BUDGET_MEM_GB``
    force the detected values (useful for pinning); ``PER_WORKER_RSS_MB``,
    ``RESOURCE_HEADROOM``, ``PG_CONN_MARGIN``, ``ASGI_DB_THREADS`` tune the formula.
    """
    # --- CPU ---
    forced_cpu = _env(env, "RESOURCE_BUDGET_CPUS")
    if forced_cpu:
        cpu_budget, cpu_source = float(forced_cpu), "env"
    else:
        affinity_cpus = _affinity_cpus(affinity)
        quota = _cgroup_v2_cpu(cgroup_mount, proc_cgroup)
        cpu_source = "cgroup-v2"
        if quota is None:
            quota = _cgroup_v1_cpu(cgroup_mount)
            cpu_source = "cgroup-v1"
        if quota is None:
            cpu_budget, cpu_source = float(affinity_cpus), "affinity"
        else:
            # A quota can exceed the affinity mask (or vice-versa); the binding
            # limit is the smaller of the two.
            cpu_budget = min(quota, float(affinity_cpus))

    # --- RAM ---
    forced_mem = _env(env, "RESOURCE_BUDGET_MEM_GB")
    if forced_mem:
        ram_bytes, mem_source = int(float(forced_mem) * _BYTES_PER_GIB), "env"
    else:
        phys = _meminfo_total_bytes(proc_meminfo)
        cg_mem = _cgroup_v2_mem(cgroup_mount, proc_cgroup)
        mem_source = "cgroup-v2"
        if cg_mem is None:
            cg_mem = _cgroup_v1_mem(cgroup_mount)
            mem_source = "cgroup-v1"
        if cg_mem is None:
            if phys is None:
                # Last-resort floor so we never divide by zero downstream.
                ram_bytes, mem_source = 2 * _BYTES_PER_GIB, "default"
            else:
                ram_bytes, mem_source = phys, "meminfo"
        else:
            # Physical RAM caps an over-generous cgroup limit.
            ram_bytes = min(cg_mem, phys) if phys else cg_mem

    # --- formula knobs ---
    rss_mb = _env(env, "PER_WORKER_RSS_MB")
    per_worker_rss = int(float(rss_mb) * _BYTES_PER_MB) if rss_mb else DEFAULT_PER_WORKER_RSS_MB * _BYTES_PER_MB
    hr = _env(env, "RESOURCE_HEADROOM")
    headroom = float(hr) if hr else DEFAULT_HEADROOM
    margin = _env(env, "PG_CONN_MARGIN")
    pg_margin = int(margin) if margin else DEFAULT_PG_MARGIN
    dbt = _env(env, "ASGI_DB_THREADS")
    db_threads = int(dbt) if dbt else None

    return compute_sizing(
        cpu_budget,
        ram_bytes,
        per_worker_rss=per_worker_rss,
        headroom=headroom,
        cpu_source=cpu_source,
        mem_source=mem_source,
        db_threads=db_threads,
        pg_margin=pg_margin,
    )


# ---------------------------------------------------------------------------
# CLI — consumed by service entrypoints
# ---------------------------------------------------------------------------

_VALUE_FIELDS = {
    "workers": lambda b: b.workers,
    "asgi_threads": lambda b: b.asgi_threads,
    "db_threads": lambda b: b.db_threads,
    "pg_max_conns": lambda b: b.pg_max_conns,
    "scanner_pool": lambda b: b.scanner_pool,
    "bedrock_pool": lambda b: b.bedrock_pool,
    "vault_pool": lambda b: b.vault_pool,
    "cpu_budget": lambda b: round(b.cpu_budget, 3),
    "ram_gib": lambda b: round(b.ram_gib, 2),
}


def _main(argv: Optional[list] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="AI Mesh resource budget detector")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--json", action="store_true", help="print the full budget as JSON")
    group.add_argument("--export", action="store_true", help="print KEY=VALUE lines for eval")
    group.add_argument("--value", choices=sorted(_VALUE_FIELDS), help="print a single field")
    args = parser.parse_args(argv)

    budget = detect()

    if args.value:
        print(_VALUE_FIELDS[args.value](budget))
    elif args.export:
        print(f"WEB_CONCURRENCY={budget.workers}")
        print(f"ASGI_THREADS={budget.asgi_threads}")
        print(f"PG_MAX_CONNS={budget.pg_max_conns}")
        print(f"GATEWAY_SCANNER_THREAD_POOL_SIZE={budget.scanner_pool}")
        print(f"GATEWAY_BEDROCK_THREAD_POOL_SIZE={budget.bedrock_pool}")
        print(f"GATEWAY_VAULT_POOL_MAX={budget.vault_pool}")
        print(f"CPU_BUDGET={round(budget.cpu_budget, 3)}")
    else:  # default and --json
        print(json.dumps(budget.as_dict(), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
