"""LGW03-1..6 in-process ResourceContract gates."""

from __future__ import annotations

import os
import signal
import threading
import time
from pathlib import Path

import pytest

from gateway_v2.runtime.cgroup import DetectHooks, detect_with_hooks
from gateway_v2.runtime.errors import CapacityUnavailable, CapacityUnset
from gateway_v2.runtime.kinds import PoolKind
from gateway_v2.runtime.lifecycle import install_sighup
from gateway_v2.runtime.pools import GatewayRuntime
from gateway_v2.runtime.resources import load_contract

_RSS = "400"
_MEM_ROOM = str(64 * 400 * 1024 * 1024)
_FD = "65536"


def _env(cpu: str, mem: str = _MEM_ROOM, fd: str = _FD, **extra: str) -> dict[str, str]:
    base = {
        "AMF_CPU_QUOTA": cpu,
        "AMF_MEMORY_LIMIT_BYTES": mem,
        "AMF_FD_LIMIT": fd,
        "AMF_PER_WORKER_RSS_MB": _RSS,
        "AMF_UTILIZATION_CAP": "0.75",
        "AMF_TARGET_P99_MS": "20",
    }
    base.update(extra)
    return base


def _snap(cpu: str, **extra: str) -> dict[str, object]:
    contract, logs = load_contract(env=_env(cpu, **extra))
    rate = contract.offered_service_rate()
    return {
        "workers": contract.workers(),
        "queue_depth": contract.queue_depth(rate),
        "scanner": contract.pool_size(PoolKind.SCANNER),
        "provider": contract.pool_size(PoolKind.PROVIDER),
        "vault": contract.pool_size(PoolKind.VAULT),
        "redis": contract.pool_size(PoolKind.REDIS),
        "buffer": contract.stream_buffer_bytes(1),
        "connections": contract.connection_budget(),
        "logs": logs,
        "contract": contract,
    }


def test_lgw03_1_four_cpu_points_differ() -> None:
    rows = {n: _snap(str(n)) for n in (2, 4, 8, 16)}
    worker_vals = {rows[n]["workers"] for n in rows}
    queue_vals = {rows[n]["queue_depth"] for n in rows}
    scanner_vals = {rows[n]["scanner"] for n in rows}
    assert len(worker_vals) == 4, rows
    assert len(queue_vals) == 4, rows
    assert len(scanner_vals) == 4, rows
    assert rows[2]["workers"] == 1
    assert rows[4]["workers"] == 3
    assert rows[8]["workers"] == 6
    assert rows[16]["workers"] == 12


def test_lgw03_2_halve_memory_buffers_and_queue_fall() -> None:
    high_mem = str(1600 * 1024 * 1024)
    low_mem = str(800 * 1024 * 1024)
    high = _snap("8", mem=high_mem)
    low = _snap("8", mem=low_mem)
    assert int(low["buffer"]) < int(high["buffer"])
    assert int(low["queue_depth"]) < int(high["queue_depth"])
    assert int(low["workers"]) < int(high["workers"])


def test_lgw03_3_lower_fd_connection_budget_falls() -> None:
    high = _snap("8", fd="10000")
    low = _snap("8", fd="100")
    assert int(low["connections"]) < int(high["connections"])
    joined = "\n".join(low["logs"])
    assert "fd_limit=100" in joined
    assert "source=env" in joined


def test_lgw03_6_web_concurrency_logged_as_deviation() -> None:
    snap = _snap("16", WEB_CONCURRENCY="3")
    assert snap["workers"] == 3
    joined = "\n".join(snap["logs"])
    assert "WEB_CONCURRENCY override=3" in joined
    assert "detected=12" in joined
    assert "(deviation)" in joined


def test_refuse_start_when_workers_below_one() -> None:
    with pytest.raises(CapacityUnavailable, match="refuse to start"):
        load_contract(env=_env("0.1"))


def test_guard_pool_unset_not_guessed() -> None:
    contract, _logs = load_contract(env=_env("4"))
    with pytest.raises(CapacityUnset, match="unset"):
        contract.pool_size(PoolKind.GUARD)


def test_lgw03_5_sighup_resizes_without_drop() -> None:
    env = _env("2")
    contract, logs = load_contract(env=env)
    runtime = GatewayRuntime(contract, logs, env=env)
    pool = runtime.pools[PoolKind.SCANNER]
    pool.acquire()
    assert runtime.in_flight() == 1
    prev = signal.getsignal(signal.SIGHUP)
    env["AMF_CPU_QUOTA"] = "16"
    install_sighup(runtime)
    try:
        os.kill(os.getpid(), signal.SIGHUP)
        deadline = time.monotonic() + 2.0
        while runtime.reload_count < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        signal.signal(signal.SIGHUP, prev)
    assert runtime.reload_count >= 1
    assert runtime.pid == os.getpid()
    assert runtime.dropped() == 0
    assert runtime.in_flight() == 1
    assert runtime.contract.workers() == 12
    pool.release()
    assert pool.completed == 1
    assert runtime.dropped() == 0


def test_inflight_soft_shrink() -> None:
    env = _env("16")
    contract, logs = load_contract(env=env)
    runtime = GatewayRuntime(contract, logs, env=env)
    pool = runtime.pools[PoolKind.SCANNER]
    pool.acquire()
    env["AMF_CPU_QUOTA"] = "2"
    runtime.reload()
    assert runtime.dropped() == 0
    assert pool.held == 1
    done = threading.Event()

    def _release() -> None:
        time.sleep(0.05)
        pool.release()
        done.set()

    threading.Thread(target=_release, daemon=True).start()
    done.wait(timeout=2.0)
    assert pool.completed == 1
    assert runtime.dropped() == 0


def test_cgroup_v2_cpu_max_injectable(tmp_path: Path) -> None:
    (tmp_path / "cpu.max").write_text("200000 100000\n", encoding="utf-8")
    (tmp_path / "memory.max").write_text("max\n", encoding="utf-8")
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:        1048576 kB\n", encoding="utf-8")
    hooks = DetectHooks(
        cgroup_mount=str(tmp_path),
        proc_cgroup=str(tmp_path / "missing-cgroup"),
        proc_meminfo=str(meminfo),
        rlimit_nofile=lambda: (1024, 1024),
    )
    signals = detect_with_hooks(env={}, hooks=hooks)
    assert signals.cpu_quota == pytest.approx(2.0)
    assert signals.cpu_source == "cgroup-v2"
    assert signals.memory_limit == 1048576 * 1024
    assert signals.mem_source == "meminfo"
    assert signals.fd_source == "injected"
