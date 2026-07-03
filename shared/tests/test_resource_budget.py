"""Unit tests for the cgroup-aware resource budget detector.

The detector reads real files, so every test fabricates a fake cgroup mount +
``/proc`` sources in a tmp dir and injects them — no container required. This
proves the sizing formula on 6c/16G, 12c/60G, and an unconstrained host, plus
the RAM-bound and env-override edge cases the whole design hinges on.
"""

from __future__ import annotations

import os

import pytest

from ai_mesh_shared import resource_budget as rb

GIB = 1024 * 1024 * 1024
MB = 1024 * 1024


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(content)


def _make_v2_mount(tmp_path, cpu_max=None, memory_max=None, meminfo_gb=64):
    """Build a fake cgroup-v2 mount + /proc sources under tmp_path.

    Returns kwargs for rb.detect(). cpu_max/memory_max are the raw file contents
    (None → omit the file, i.e. controller not present → detector falls back).
    """
    mount = os.path.join(tmp_path, "cgroup")
    proc_cgroup = os.path.join(tmp_path, "self_cgroup")
    proc_meminfo = os.path.join(tmp_path, "meminfo")
    _write(proc_cgroup, "0::/\n")
    _write(proc_meminfo, f"MemTotal:       {int(meminfo_gb * GIB // 1024)} kB\nMemFree: 1 kB\n")
    if cpu_max is not None:
        _write(os.path.join(mount, "cpu.max"), cpu_max + "\n")
    if memory_max is not None:
        _write(os.path.join(mount, "memory.max"), memory_max + "\n")
    os.makedirs(mount, exist_ok=True)
    return {
        "cgroup_mount": mount,
        "proc_cgroup": proc_cgroup,
        "proc_meminfo": proc_meminfo,
        "env": {},
    }


# --------------------------------------------------------------------------
# The three canonical profiles from the mandate
# --------------------------------------------------------------------------

def test_6c_16g_container(tmp_path):
    # 6 CPUs => cpu.max "600000 100000"; 16 GiB memory.max.
    kw = _make_v2_mount(tmp_path, cpu_max="600000 100000", memory_max=str(16 * GIB), meminfo_gb=64)
    b = rb.detect(affinity=lambda: 64, **kw)

    assert b.cpu_source == "cgroup-v2"
    assert b.mem_source == "cgroup-v2"
    assert b.cpu_budget == pytest.approx(6.0)
    assert b.ram_bytes == 16 * GIB
    # 16 GiB * 0.75 / 512 MiB = 24 >> 6 cores -> CPU-bound at 6 workers.
    assert b.workers == 6
    assert b.asgi_threads == 12          # clamp(6*2, 8, 32)
    assert b.pg_max_conns == 6 * (12 + 1) + 50  # 128


def test_12c_60g_container(tmp_path):
    kw = _make_v2_mount(tmp_path, cpu_max="1200000 100000", memory_max=str(60 * GIB), meminfo_gb=64)
    b = rb.detect(affinity=lambda: 64, **kw)

    assert b.cpu_budget == pytest.approx(12.0)
    assert b.ram_bytes == 60 * GIB
    assert b.workers == 12               # 60*0.75/0.5 = 90 >> 12 cores
    assert b.asgi_threads == 24          # clamp(12*2, 8, 32)
    assert b.pg_max_conns == 12 * (24 + 1) + 50  # 350
    assert b.as_dict()["cpu_bound"] is True


def test_unconstrained_host(tmp_path):
    # No cpu.max / memory.max files -> fall back to affinity + meminfo.
    kw = _make_v2_mount(tmp_path, cpu_max=None, memory_max=None, meminfo_gb=60)
    b = rb.detect(affinity=lambda: 16, **kw)

    assert b.cpu_source == "affinity"
    assert b.mem_source == "meminfo"
    assert b.cpu_budget == pytest.approx(16.0)
    assert b.ram_bytes == pytest.approx(60 * GIB, rel=0.001)
    assert b.workers == 16
    assert b.asgi_threads == 32          # clamp(16*2=32, 8, 32)


def test_cpu_max_literal_falls_back_to_affinity(tmp_path):
    # cpu.max == "max" (no quota) must behave like unconstrained.
    kw = _make_v2_mount(tmp_path, cpu_max="max 100000", memory_max="max", meminfo_gb=60)
    b = rb.detect(affinity=lambda: 16, **kw)
    assert b.cpu_source == "affinity"
    assert b.mem_source == "meminfo"
    assert b.workers == 16


# --------------------------------------------------------------------------
# RAM ceiling actually binds
# --------------------------------------------------------------------------

def test_ram_bounded_below_cpu(tmp_path):
    # 16 cores but only 4 GiB: 4*0.75/0.5 = 6 workers -> RAM binds, not cores.
    kw = _make_v2_mount(tmp_path, cpu_max="1600000 100000", memory_max=str(4 * GIB), meminfo_gb=64)
    b = rb.detect(affinity=lambda: 64, **kw)
    assert b.cpu_budget == pytest.approx(16.0)
    assert b.workers == 6                # min(16, 6)
    assert b.as_dict()["cpu_bound"] is False


def test_tiny_box_hits_min_floor(tmp_path):
    # 1 core, 1 GiB -> both signals say 1/1, but floor keeps 2 workers.
    kw = _make_v2_mount(tmp_path, cpu_max="100000 100000", memory_max=str(1 * GIB), meminfo_gb=64)
    b = rb.detect(affinity=lambda: 64, **kw)
    assert b.cpu_budget == pytest.approx(1.0)
    assert b.workers == 2                # clamp floor
    assert b.asgi_threads == 8           # clamp(2, 8, 32) -> 8 floor


def test_physical_ram_caps_bogus_cgroup_limit(tmp_path):
    # cgroup memory.max is a near-int64 "unlimited" sentinel -> use meminfo.
    kw = _make_v2_mount(tmp_path, cpu_max="800000 100000",
                        memory_max=str((1 << 63) - 1), meminfo_gb=32)
    b = rb.detect(affinity=lambda: 64, **kw)
    assert b.mem_source == "meminfo"
    assert b.ram_bytes == pytest.approx(32 * GIB, rel=0.001)


def test_affinity_caps_quota(tmp_path):
    # Quota says 32 CPUs but affinity mask only exposes 4 -> min wins.
    kw = _make_v2_mount(tmp_path, cpu_max="3200000 100000", memory_max=str(32 * GIB), meminfo_gb=64)
    b = rb.detect(affinity=lambda: 4, **kw)
    assert b.cpu_budget == pytest.approx(4.0)
    assert b.workers == 4


# --------------------------------------------------------------------------
# Env overrides & formula knobs
# --------------------------------------------------------------------------

def test_forced_env_budget(tmp_path):
    kw = _make_v2_mount(tmp_path, cpu_max="600000 100000", memory_max=str(16 * GIB))
    kw["env"] = {"RESOURCE_BUDGET_CPUS": "8", "RESOURCE_BUDGET_MEM_GB": "32"}
    b = rb.detect(affinity=lambda: 64, **kw)
    assert b.cpu_source == "env"
    assert b.mem_source == "env"
    assert b.cpu_budget == pytest.approx(8.0)
    assert b.ram_bytes == 32 * GIB
    assert b.workers == 8


def test_per_worker_rss_override_tightens_ram_bound(tmp_path):
    # Push per-worker RSS to 2 GiB: 12 GiB*0.75/2 = 4.5 -> floor 4 workers.
    kw = _make_v2_mount(tmp_path, cpu_max="1200000 100000", memory_max=str(12 * GIB))
    kw["env"] = {"PER_WORKER_RSS_MB": "2048"}
    b = rb.detect(affinity=lambda: 64, **kw)
    assert b.workers == 4                # min(12, floor(12*0.75/2)=4)


def test_headroom_override(tmp_path):
    kw = _make_v2_mount(tmp_path, cpu_max="1600000 100000", memory_max=str(8 * GIB))
    kw["env"] = {"RESOURCE_HEADROOM": "0.5", "PER_WORKER_RSS_MB": "512"}
    b = rb.detect(affinity=lambda: 64, **kw)
    # 8 GiB * 0.5 / 0.5 GiB = 8 workers (min with 16 cores).
    assert b.workers == 8


def test_pg_margin_and_db_threads_override(tmp_path):
    kw = _make_v2_mount(tmp_path, cpu_max="600000 100000", memory_max=str(16 * GIB))
    kw["env"] = {"PG_CONN_MARGIN": "100", "ASGI_DB_THREADS": "10"}
    b = rb.detect(affinity=lambda: 64, **kw)
    assert b.db_threads == 10
    assert b.pg_max_conns == 6 * (10 + 1) + 100  # 166


# --------------------------------------------------------------------------
# cgroup v1 path
# --------------------------------------------------------------------------

def test_cgroup_v1_cpu_and_mem(tmp_path):
    mount = os.path.join(tmp_path, "cgroup")
    proc_cgroup = os.path.join(tmp_path, "self_cgroup")
    proc_meminfo = os.path.join(tmp_path, "meminfo")
    _write(proc_cgroup, "3:cpu,cpuacct:/docker/abc\n1:memory:/docker/abc\n")
    _write(proc_meminfo, f"MemTotal:       {64 * GIB // 1024} kB\n")
    _write(os.path.join(mount, "cpu", "cpu.cfs_quota_us"), "600000\n")
    _write(os.path.join(mount, "cpu", "cpu.cfs_period_us"), "100000\n")
    _write(os.path.join(mount, "memory", "memory.limit_in_bytes"), str(16 * GIB) + "\n")

    b = rb.detect(cgroup_mount=mount, proc_cgroup=proc_cgroup,
                  proc_meminfo=proc_meminfo, env={}, affinity=lambda: 64)
    assert b.cpu_source == "cgroup-v1"
    assert b.mem_source == "cgroup-v1"
    assert b.cpu_budget == pytest.approx(6.0)
    assert b.ram_bytes == 16 * GIB
    assert b.workers == 6


def test_cgroup_v1_unlimited_mem_falls_back(tmp_path):
    mount = os.path.join(tmp_path, "cgroup")
    proc_meminfo = os.path.join(tmp_path, "meminfo")
    _write(proc_meminfo, f"MemTotal:       {48 * GIB // 1024} kB\n")
    _write(os.path.join(mount, "memory", "memory.limit_in_bytes"), "9223372036854771712\n")
    b = rb.detect(cgroup_mount=mount, proc_cgroup=os.path.join(tmp_path, "none"),
                  proc_meminfo=proc_meminfo, env={}, affinity=lambda: 8)
    assert b.mem_source == "meminfo"
    assert b.ram_bytes == pytest.approx(48 * GIB, rel=0.001)


# --------------------------------------------------------------------------
# Pure formula unit
# --------------------------------------------------------------------------

def test_compute_sizing_direct():
    b = rb.compute_sizing(12.0, 60 * GIB, per_worker_rss=512 * MB)
    assert b.workers == 12
    assert b.asgi_threads == 24
    assert b.pg_max_conns == 350
    assert b.ram_workers == 90


def test_compute_sizing_zero_rss_guard():
    b = rb.compute_sizing(4.0, 8 * GIB, per_worker_rss=0)
    assert b.workers == 4  # no div-by-zero; RAM bound disabled


# --------------------------------------------------------------------------
# Gateway per-worker offload pools (item 10) — clamped to bound N-worker total
# --------------------------------------------------------------------------

def test_gateway_pools_scale_and_clamp():
    # scanner=clamp(round(cpu),4,16); bedrock==asgi_threads; vault=clamp(round(cpu/2),2,8)
    b6 = rb.compute_sizing(6.0, 16 * GIB)
    assert (b6.scanner_pool, b6.bedrock_pool, b6.vault_pool) == (6, 12, 3)
    b12 = rb.compute_sizing(12.0, 60 * GIB)
    assert (b12.scanner_pool, b12.bedrock_pool, b12.vault_pool) == (12, 24, 6)
    b16 = rb.compute_sizing(16.0, 60 * GIB)         # clamps bite: scanner 16, bedrock 32, vault 8
    assert (b16.scanner_pool, b16.bedrock_pool, b16.vault_pool) == (16, 32, 8)


def test_gateway_pools_floor_on_tiny_box():
    b1 = rb.compute_sizing(1.0, 2 * GIB)            # floors: scanner 4, bedrock 8, vault 2
    assert (b1.scanner_pool, b1.bedrock_pool, b1.vault_pool) == (4, 8, 2)


def test_redis_pool_scales_and_clamps():
    # redis_pool = clamp(asgi_threads*4, 64, 256); asgi_threads = clamp(round(cpu*2),8,32)
    assert rb.compute_sizing(6.0, 16 * GIB).redis_pool == 64    # clamp(12*4=48,64,256)=64 floor
    assert rb.compute_sizing(12.0, 60 * GIB).redis_pool == 96   # clamp(24*4=96,64,256)=96
    assert rb.compute_sizing(16.0, 60 * GIB).redis_pool == 128  # clamp(32*4=128,64,256)=128
    assert rb.compute_sizing(1.0, 2 * GIB).redis_pool == 64     # clamp(8*4=32,64,256)=64 floor


def test_gateway_pools_via_detect_and_cli(tmp_path):
    kw = _make_v2_mount(tmp_path, cpu_max="1200000 100000", memory_max=str(60 * GIB))
    b = rb.detect(affinity=lambda: 64, **kw)
    assert b.scanner_pool == 12 and b.bedrock_pool == 24 and b.vault_pool == 6
    # CLI --value surfaces them for the entrypoint export.
    assert rb._VALUE_FIELDS["scanner_pool"](b) == 12
    assert rb._VALUE_FIELDS["vault_pool"](b) == 6
