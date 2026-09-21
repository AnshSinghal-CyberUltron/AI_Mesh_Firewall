"""Cgroup v2 → v1 → affinity → RLIMIT detection. No sizing clamps."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from gateway_v2.runtime.errors import CapacityUnavailable
from gateway_v2.runtime.kinds import HardwareSignals

_CGROUP_MOUNT = "/sys/fs/cgroup"
_UNLIMITED = 1 << 62
_BYTES_PER_GIB = 1024**3


@dataclass(frozen=True, slots=True)
class DetectHooks:
    cgroup_mount: str = _CGROUP_MOUNT
    proc_cgroup: str = "/proc/self/cgroup"
    proc_meminfo: str = "/proc/meminfo"
    affinity: Callable[[], int] | None = None
    rlimit_nofile: Callable[[], tuple[int, int]] | None = None


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _parse_quota_period(text: str) -> float | None:
    parts = text.split()
    if not parts:
        return None
    quota_s = parts[0]
    if quota_s == "max":
        return None
    try:
        quota = int(quota_s)
    except ValueError:
        return None
    period = 100000
    if len(parts) > 1:
        try:
            period = int(parts[1])
        except ValueError:
            return None
    if quota < 0 or period <= 0:
        return None
    return quota / period


def _cgroup_v2_cpu(cgroup_mount: str, proc_cgroup: str) -> float | None:
    direct = _parse_cpu_max(Path(cgroup_mount) / "cpu.max")
    if direct is not None:
        return direct
    rel = _v2_relpath(proc_cgroup)
    if rel is None:
        return None
    return _parse_cpu_max(Path(cgroup_mount) / rel / "cpu.max")


def _parse_cpu_max(path: Path) -> float | None:
    raw = _read(path)
    if raw is None:
        return None
    return _parse_quota_period(raw)


def _v2_relpath(proc_cgroup: str) -> str | None:
    raw = _read(Path(proc_cgroup))
    if raw is None:
        return None
    for line in raw.splitlines():
        parts = line.split(":")
        if len(parts) >= 3 and parts[1] == "":
            rel = parts[2].lstrip("/")
            return rel
    return None


def _cgroup_v1_cpu(cgroup_mount: str) -> float | None:
    quota_raw = _read(Path(cgroup_mount) / "cpu" / "cpu.cfs_quota_us")
    period_raw = _read(Path(cgroup_mount) / "cpu" / "cpu.cfs_period_us")
    if quota_raw is None or period_raw is None:
        return None
    try:
        quota = int(quota_raw)
        period = int(period_raw)
    except ValueError:
        return None
    if quota < 0 or period <= 0:
        return None
    return quota / period


def _parse_mem_bytes(raw: str | None) -> int | None:
    if raw is None or raw == "max":
        return None
    try:
        n = int(raw)
    except ValueError:
        return None
    if n <= 0 or n >= _UNLIMITED:
        return None
    return n


def _cgroup_v2_mem(cgroup_mount: str, proc_cgroup: str) -> int | None:
    direct = _parse_mem_bytes(_read(Path(cgroup_mount) / "memory.max"))
    if direct is not None:
        return direct
    rel = _v2_relpath(proc_cgroup)
    if rel is None:
        return None
    return _parse_mem_bytes(_read(Path(cgroup_mount) / rel / "memory.max"))


def _cgroup_v1_mem(cgroup_mount: str) -> int | None:
    return _parse_mem_bytes(_read(Path(cgroup_mount) / "memory" / "memory.limit_in_bytes"))


def _meminfo_total_bytes(proc_meminfo: str) -> int | None:
    raw = _read(Path(proc_meminfo))
    if raw is None:
        return None
    for line in raw.splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    return int(parts[1]) * 1024
                except ValueError:
                    return None
    return None


def _affinity_cpus(affinity: Callable[[], int] | None) -> int:
    if affinity is not None:
        n = affinity()
        if n < 1:
            raise CapacityUnavailable("affinity reported fewer than one CPU")
        return n
    try:
        n = len(os.sched_getaffinity(0))
    except (AttributeError, OSError) as exc:
        raise CapacityUnavailable(f"CPU affinity unreadable: {exc}") from exc
    if n < 1:
        raise CapacityUnavailable("affinity reported fewer than one CPU")
    return n


def _fd_limit(rlimit_nofile: Callable[[], tuple[int, int]] | None) -> tuple[int, str]:
    if rlimit_nofile is not None:
        soft, _hard = rlimit_nofile()
        if soft < 1:
            raise CapacityUnavailable("RLIMIT_NOFILE below one")
        return int(soft), "injected"
    try:
        import resource

        soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    except (ImportError, OSError, ValueError) as exc:
        raise CapacityUnavailable(f"RLIMIT_NOFILE unreadable: {exc}") from exc
    if soft < 1:
        raise CapacityUnavailable("RLIMIT_NOFILE below one")
    return int(soft), "rlimit"


def _env(env: dict[str, str] | None, key: str) -> str | None:
    src = os.environ if env is None else env
    val = src.get(key)
    if val is None:
        return None
    stripped = val.strip()
    return stripped or None


def _cpu_quota(
    env: dict[str, str] | None,
    cgroup_mount: str,
    proc_cgroup: str,
    affinity: Callable[[], int] | None,
) -> tuple[float, str]:
    forced = _env(env, "AMF_CPU_QUOTA") or _env(env, "RESOURCE_BUDGET_CPUS")
    if forced:
        return float(forced), "env"
    quota = _cgroup_v2_cpu(cgroup_mount, proc_cgroup)
    source = "cgroup-v2"
    if quota is None:
        quota = _cgroup_v1_cpu(cgroup_mount)
        source = "cgroup-v1"
    if quota is None:
        return float(_affinity_cpus(affinity)), "affinity"
    return quota, source


def _memory_limit(
    env: dict[str, str] | None,
    cgroup_mount: str,
    proc_cgroup: str,
    proc_meminfo: str,
) -> tuple[int, str]:
    forced_bytes = _env(env, "AMF_MEMORY_LIMIT_BYTES")
    if forced_bytes:
        return int(forced_bytes), "env"
    forced_gb = _env(env, "RESOURCE_BUDGET_MEM_GB")
    if forced_gb:
        return int(float(forced_gb) * _BYTES_PER_GIB), "env"
    phys = _meminfo_total_bytes(proc_meminfo)
    cg_mem = _cgroup_v2_mem(cgroup_mount, proc_cgroup)
    source = "cgroup-v2"
    if cg_mem is None:
        cg_mem = _cgroup_v1_mem(cgroup_mount)
        source = "cgroup-v1"
    if cg_mem is None:
        if phys is None:
            raise CapacityUnavailable(
                "memory limit unreadable (cgroup and /proc/meminfo both failed)",
            )
        return phys, "meminfo"
    if phys is None:
        return cg_mem, source
    return min(cg_mem, phys), source


def detect(
    *,
    env: dict[str, str] | None = None,
    cgroup_mount: str = _CGROUP_MOUNT,
    proc_cgroup: str = "/proc/self/cgroup",
    proc_meminfo: str = "/proc/meminfo",
    affinity: Callable[[], int] | None = None,
    rlimit_nofile: Callable[[], tuple[int, int]] | None = None,
) -> HardwareSignals:
    """Detect CPU, memory, and fd. Fallback order is logged via source fields."""
    cpu_quota, cpu_source = _cpu_quota(env, cgroup_mount, proc_cgroup, affinity)
    memory_limit, mem_source = _memory_limit(env, cgroup_mount, proc_cgroup, proc_meminfo)
    forced_fd = _env(env, "AMF_FD_LIMIT")
    if forced_fd:
        fd_limit, fd_source = int(forced_fd), "env"
        if fd_limit < 1:
            raise CapacityUnavailable("AMF_FD_LIMIT below one")
    else:
        fd_limit, fd_source = _fd_limit(rlimit_nofile)
    return HardwareSignals(
        cpu_quota=cpu_quota,
        cpu_source=cpu_source,
        memory_limit=memory_limit,
        mem_source=mem_source,
        fd_limit=fd_limit,
        fd_source=fd_source,
    )


def detect_with_hooks(
    *,
    env: dict[str, str] | None = None,
    hooks: DetectHooks | None = None,
) -> HardwareSignals:
    h = hooks if hooks is not None else DetectHooks()
    return detect(
        env=env,
        cgroup_mount=h.cgroup_mount,
        proc_cgroup=h.proc_cgroup,
        proc_meminfo=h.proc_meminfo,
        affinity=h.affinity,
        rlimit_nofile=h.rlimit_nofile,
    )
