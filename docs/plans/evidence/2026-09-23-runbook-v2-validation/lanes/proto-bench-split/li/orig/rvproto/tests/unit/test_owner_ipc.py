"""Owner topologies over real sockets to real owner PROCESSES (fake engine): framing and frame
validation, Unix-socket and TCP round trips, relative budgets, cancel propagation, owner death =>
UNAVAILABLE (never clean) and reconnect, the owner's bounded queue => SHED, refusal of an owner
running another model, least-outstanding routing + failover across several TCP owners, and a
silent owner timed out and disconnected."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from tokenizers import Tokenizer

from rvproto.detect.guard.factory import sharing
from rvproto.detect.guard.ipc_client import OwnerClientBackend
from rvproto.detect.guard.wire import (
    Endpoint,
    FrameError,
    Geometry,
    decode_request,
    decode_response,
    encode_request,
    encode_response,
    socket_path,
)
from rvproto.domain.guard import Budget, GuardResult
from rvproto.runtime.config import load_settings
from rvproto.runtime.metrics import Registry

ROOT = Path(__file__).resolve().parents[2]
TOKENIZER = os.environ.get("RV_GUARD_TOKENIZER") or str(
    ROOT.parent / "models" / "Llama-Prompt-Guard-2-22M" / "tokenizer.json")
GEO = Geometry(max_windows=16, window_tokens=512, vocab=1000)


def test_frames_round_trip_and_validation() -> None:
    rows = [[1, 2, 3], [7], list(range(510))]
    frame = encode_request(42, 123456789, rows)
    req_id, budget, got = decode_request(frame[4:], GEO)
    assert (req_id, budget, [list(map(int, r)) for r in got]) == (42, 123456789, rows)
    assert decode_request(encode_request(9, -5, [])[4:], GEO) == (9, -5, [])  # cancel / spent budget
    for bad_rows in ([[1]] * 17, [list(range(513))], [[1000]], [[-1]]):  # geometry + vocabulary
        with pytest.raises(FrameError):
            decode_request(encode_request(1, 1, bad_rows)[4:], GEO)
    with pytest.raises(FrameError):
        decode_request(encode_request(1, 1, [[1, 2]])[4:-4], GEO)  # truncated body
    res = GuardResult(True, (0.25, 0.5), 11, 22, 2, "detail")
    assert decode_response(encode_response(42, res)[4:], GEO) == (42, res)
    bad = GuardResult(False, (), 0, 0, 0, "guard deadline passed while queued")
    assert decode_response(encode_response(7, bad)[4:], GEO) == (7, bad)
    shed = GuardResult(False, (), 0, 0, 0, "guard owner queue full", retry_after_s=0.0625)
    assert decode_response(encode_response(8, shed)[4:], GEO) == (8, shed)
    with pytest.raises(FrameError):
        decode_response(encode_response(8, res)[4:-1], GEO)
    assert sharing(18, 2) == 9 and sharing(3, 2) == 2 and sharing(1, 4) == 1


@pytest.fixture()
def sock_dir() -> Iterator[str]:
    d = tempfile.mkdtemp(prefix="rvt-", dir=os.environ.get("XDG_RUNTIME_DIR") or None)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _free_ports(n: int) -> int:
    """A base port with n consecutive free ports on loopback."""
    for _ in range(50):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            base = probe.getsockname()[1]
        socks = []
        try:
            for i in range(n):
                s = socket.socket()
                socks.append(s)
                s.bind(("127.0.0.1", base + i))
            return base
        except OSError:
            continue
        finally:
            for s in socks:
                s.close()
    raise RuntimeError("no free port range")


def _spawn(sock_dir: str, log: str, index: int = 0, **env: str) -> subprocess.Popen[bytes]:
    full = dict(os.environ, RV_GUARD_SOCKET_DIR=sock_dir, RV_GUARD_TOPOLOGY="owner", FAKE_LOG=log,
                RV_GUARD_TOKENIZER=TOKENIZER, PYTHONPATH=str(ROOT), **env)
    return subprocess.Popen([sys.executable, "-m", "tests.unit.fake_owner", str(index)], env=full, cwd=ROOT)


def _client(sock_dir: str, eps: list[Endpoint], sharing_: int, model_hash: str = "fake-hash",
            **env: str) -> tuple[OwnerClientBackend, Registry]:
    s = load_settings({"RV_GUARD_SOCKET_DIR": sock_dir, "RV_GUARD_TOPOLOGY": "owner", "RV_KS_REFRESH_MS": "100",
                       **env})
    metrics = Registry(0)
    return OwnerClientBackend(s, metrics, endpoints=eps, sharing=sharing_, model_hash=model_hash,
                              name="t(owner)"), metrics


def _unix(sock_dir: str, index: int = 0) -> list[Endpoint]:
    return [Endpoint("unix", socket_path(sock_dir, index))]


async def _until(cond: object, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():  # type: ignore[operator]
            return True
        await asyncio.sleep(0.02)
    return False


def _far() -> Budget:
    return Budget(time.perf_counter_ns() + int(30e9))


def _kill(proc: subprocess.Popen[bytes]) -> None:
    proc.send_signal(signal.SIGKILL)
    proc.wait(5)


async def test_owner_process_round_trip_cancel_death_and_reconnect(sock_dir: str) -> None:
    log = str(Path(sock_dir) / "fake.log")
    client, metrics = _client(sock_dir, _unix(sock_dir), 2)
    far = _far()
    early = client.submit([[5]], far)  # nothing connected yet: UNAVAILABLE, never clean
    assert not early.result().ok
    proc = _spawn(sock_dir, log)
    try:
        await asyncio.wait_for(client.start(), 20)
        assert client.tokens_per_s == 512.0 and (await client.readiness()).ready
        res = await asyncio.wait_for(client.submit([[5, 1], [7]], far), 5)
        assert res.ok and res.p_malicious == pytest.approx((0.005, 0.007))
        left = int(res.detail.split("=")[1])  # the owner re-anchored the REMAINING budget
        assert (res.queue_ns, res.exec_ns) == (11, 22) and 29e9 < left <= 30e9
        # client cancel -> cancel frame -> the owner cancels the queued work
        held = client.submit([[999]], far)
        await asyncio.sleep(0.2)
        held.cancel()
        assert await _until(lambda: Path(log).exists() and "cancelled" in Path(log).read_text(), 5)
        assert client.queue_stats()[0] == 0 and metrics.count["guard_cancelled_skipped"] == 1
        # owner death with work in flight -> UNAVAILABLE; new work UNAVAILABLE; not ready
        inflight = client.submit([[999]], far)
        await asyncio.sleep(0.2)
        assert client.queue_stats()[:2] == (1, 1)
        _kill(proc)
        dead = await asyncio.wait_for(inflight, 5)
        assert not dead.ok and "unreachable" in dead.detail
        assert not (await client.submit([[5]], far)).ok
        assert not (await client.readiness()).ready
        # restart -> the client reconnects by itself and serves again
        proc = _spawn(sock_dir, log)
        assert await _until(lambda: client.links[0].writer is not None, 20)
        again = await asyncio.wait_for(client.submit([[9]], far), 5)
        assert again.ok and again.p_malicious == pytest.approx((0.009,))
        assert metrics.count["guard_owner_connects"] == 2 and metrics.count["guard_owner_disconnects"] == 1
        assert f"pid={proc.pid}" in (await client.readiness()).detail  # identity of the NEW owner
    finally:
        _kill(proc)
        await client.close()


async def test_owner_running_another_model_is_refused(sock_dir: str) -> None:
    client, metrics = _client(sock_dir, _unix(sock_dir), 1, model_hash="pinned-hash")
    proc = _spawn(sock_dir, str(Path(sock_dir) / "fake.log"))
    try:
        start = asyncio.ensure_future(client.start())
        assert await _until(lambda: metrics.count.get("guard_owner_model_mismatch", 0) >= 2, 20)
        assert not start.done() and not (await client.readiness()).ready
        assert "fake-hash != pinned pinned-hash" in (await client.readiness()).detail
        res = await client.submit([[5]], Budget(time.perf_counter_ns() + int(5e9)))
        assert not res.ok  # UNAVAILABLE, never a result from a model other than the pinned one
        start.cancel()
    finally:
        _kill(proc)
        await client.close()


async def test_owner_queue_is_bounded_and_sheds_instead_of_queueing(sock_dir: str) -> None:
    """Fake engine: 1024 tokens/s; GW03 default p99 target => pool_size(GUARD) < one 64-token
    bucket, so the first request is accepted only because the queue is empty and the next is
    refused at once with a drain-time retry hint, not queued into deadline expiry."""
    log = str(Path(sock_dir) / "fake.log")
    client, metrics = _client(sock_dir, _unix(sock_dir), 1)
    proc = _spawn(sock_dir, log)
    try:
        await asyncio.wait_for(client.start(), 20)
        held = client.submit([[999]], _far())  # accepted into the empty queue, held by the engine
        await asyncio.sleep(0.2)
        refused = await asyncio.wait_for(client.submit([[5]], _far()), 5)
        assert not refused.ok and refused.retry_after_s == pytest.approx(64 / 1024)
        assert refused.detail == "guard owner queue full" and metrics.count["guard_owner_sheds"] == 1
        held.cancel()  # frees the queue: the next request is accepted and answered
        assert await _until(lambda: Path(log).exists() and "cancelled" in Path(log).read_text(), 5)
        ok = await asyncio.wait_for(client.submit([[5]], _far()), 5)
        assert ok.ok and ok.retry_after_s is None
    finally:
        _kill(proc)
        await client.close()


async def test_tcp_owner_serves_and_drops_malformed_frames(sock_dir: str) -> None:
    port = _free_ports(1)
    mdir = Path(sock_dir) / "metrics"
    mdir.mkdir()
    proc = _spawn(sock_dir, str(Path(sock_dir) / "fake.log"), RV_GUARD_OWNER_LISTEN=f"tcp://127.0.0.1:{port}",
                  RV_METRICS_DIR=str(mdir))
    client, metrics = _client(sock_dir, [Endpoint("tcp", "127.0.0.1", port)], 1)
    try:
        await asyncio.wait_for(client.start(), 20)
        ready = json.loads(Path(socket_path(sock_dir, 0) + ".ready").read_text())
        assert ready["endpoints"] == [f"127.0.0.1:{port}"] and not Path(socket_path(sock_dir, 0)).exists()
        sock = client.links[0].writer.get_extra_info("socket")  # type: ignore[union-attr]
        assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) and sock.getsockopt(
            socket.SOL_SOCKET, socket.SO_KEEPALIVE)
        res = await asyncio.wait_for(client.submit([[5, 1], [7]], _far()), 5)
        assert res.ok and res.p_malicious == pytest.approx((0.005, 0.007))
        vocab = Tokenizer.from_file(TOKENIZER).get_vocab_size()
        for garbage in ((1 << 31).to_bytes(4, "little"),  # oversized length prefix
                        encode_request(1, 10**9, [[vocab]])):  # token id outside the vocabulary
            raw = await asyncio.open_connection("127.0.0.1", port)
            await raw[0].readexactly(4)  # hello length
            raw[1].write(garbage)
            assert await asyncio.wait_for(raw[0].read(), 5) is not None  # drained
            assert raw[0].at_eof()  # the owner closed the connection
            raw[1].close()
        again = await asyncio.wait_for(client.submit([[9]], _far()), 5)  # other connections unaffected
        assert again.ok
        assert await _until(lambda: (mdir / "owner-0.json").exists() and json.loads(
            (mdir / "owner-0.json").read_text())["count"].get("owner_bad_frames") == 2, 5)
    finally:
        _kill(proc)
        await client.close()


async def test_several_tcp_owners_least_outstanding_and_failover(sock_dir: str) -> None:
    port = _free_ports(2)
    log = str(Path(sock_dir) / "fake.log")
    env = {"RV_GUARD_OWNER_LISTEN": f"tcp://127.0.0.1:{port}", "AMF_TARGET_P99_MS": "100000"}
    procs = [_spawn(sock_dir, log, i, **env) for i in (0, 1)]  # owner i binds port + i
    eps = [Endpoint("tcp", "127.0.0.1", port), Endpoint("tcp", "127.0.0.1", port + 1)]
    client, metrics = _client(sock_dir, eps, 4)
    try:
        await asyncio.wait_for(client.start(), 20)
        assert client.tokens_per_s == (1024 + 1024) / 4
        held = [client.submit([[999]], _far()) for _ in range(4)]
        await asyncio.sleep(0.2)
        assert [link.windows for link in client.links] == [2, 2]  # least outstanding, ties rotate
        _kill(procs[0])  # its two held requests -> UNAVAILABLE; the other owner keeps serving
        lost = await asyncio.wait_for(asyncio.gather(*held[0::2]), 5)  # submits 1 and 3 went to owner 0
        assert all(not r.ok for r in lost) and not any(f.done() for f in held[1::2])
        assert (await client.readiness()).ready and "owners 1/2 connected" in client.detail
        served = await asyncio.gather(*(client.submit([[5]], _far()) for _ in range(3)))
        assert all(r.ok for r in served) and client.links[0].writer is None
        _kill(procs[1])
        assert all(not r.ok for r in await asyncio.wait_for(asyncio.gather(*held[1::2]), 5))
        assert not (await client.readiness()).ready and not (await client.submit([[5]], _far())).ok
    finally:
        for p in procs:
            if p.poll() is None:
                _kill(p)
        await client.close()


async def test_silent_owner_is_timed_out_and_disconnected(sock_dir: str) -> None:
    """The owner never answers (held window) and sends nothing else: after twice its budget the
    request is UNAVAILABLE and the half-open-looking connection is dropped and re-established."""
    port = _free_ports(1)
    proc = _spawn(sock_dir, str(Path(sock_dir) / "fake.log"), RV_GUARD_OWNER_LISTEN=f"tcp://127.0.0.1:{port}",
                  AMF_TARGET_P99_MS="100000")
    client, metrics = _client(sock_dir, [Endpoint("tcp", "127.0.0.1", port)], 1)
    try:
        await asyncio.wait_for(client.start(), 20)
        t0 = time.perf_counter_ns()
        res = await asyncio.wait_for(client.submit([[999]], Budget(t0 + int(0.3e9))), 5)
        waited = (time.perf_counter_ns() - t0) / 1e9
        assert not res.ok and "twice the budget" in res.detail and 0.55 < waited < 1.5
        assert metrics.count["guard_owner_timeouts"] == 1
        assert await _until(lambda: metrics.count.get("guard_owner_connects", 0) == 2, 5)
        assert (await asyncio.wait_for(client.submit([[5]], _far()), 5)).ok
    finally:
        _kill(proc)
        await client.close()


def test_geometry_bounds_match_the_largest_legal_request() -> None:
    rows = [list(np.arange(512) % 1000) for _ in range(16)]
    assert len(encode_request(1, 1, rows)) - 4 == GEO.max_request
