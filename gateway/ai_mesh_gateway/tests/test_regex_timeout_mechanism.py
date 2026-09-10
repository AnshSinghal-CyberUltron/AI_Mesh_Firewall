"""The regex timeout mechanism must stay safe while getting cheap — task 2.

`_run_with_timeout` exists because Python cannot interrupt a running `re.search`.
Abandoning a daemon thread is the only way to free the caller from a backtracking
pattern. Task 2 replaces a fresh-thread-per-regex with a thread-local persistent worker;
these tests pin the three properties that must survive.

P3 is the trap. A hung job blocks its worker forever, so a NAIVE persistent worker would
queue every later regex behind it and time them all out — turning one bad pattern into a
total detection outage, strictly worse than the code being replaced.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ai_mesh_gateway"))

from ai_mesh_gateway import policy_engine as pe  # noqa: E402


def test_p1_caller_is_freed_while_the_job_is_still_running():
    """The caller must return after `timeout` even though the work never finishes."""
    release = threading.Event()
    try:
        t0 = time.perf_counter()
        out = pe._run_with_timeout(lambda: release.wait(30) or "finished", 0.15)
        waited = time.perf_counter() - t0
        assert out is None, "a job that never finished must not return a result"
        assert waited < 2.0, (
            f"caller was held {waited:.2f}s for a 0.15s timeout — the mechanism no "
            f"longer frees the caller from a runaway pattern (P1)")
    finally:
        release.set()


def test_p2_an_exception_returns_none():
    def boom():
        raise ValueError("regex blew up")
    assert pe._run_with_timeout(boom, 1.0) is None


def test_p3_a_hung_regex_does_not_poison_the_next_one():
    """THE TRAP. After one job hangs, the NEXT call must still work.

    Today this holds because the stuck thread is abandoned and the next call makes a
    fresh one. A persistent worker that is reused after a timeout would queue this
    second call behind the still-running first and time it out too — one bad pattern
    would disable detection for every subsequent rule.
    """
    release = threading.Event()
    try:
        assert pe._run_with_timeout(lambda: release.wait(30), 0.15) is None
        for i in range(5):
            got = pe._run_with_timeout(lambda i=i: f"ok{i}", 2.0)
            assert got == f"ok{i}", (
                f"call {i} after a hang returned {got!r}; the stuck worker is still "
                f"blocking the queue (P3)")
    finally:
        release.set()


def test_normal_calls_reuse_one_worker_rather_than_spawning_per_call(monkeypatch):
    """The point of the change: consecutive calls must not each CREATE a thread.

    Counting `threading.active_count()` would not detect this — a per-call thread is
    joined and gone before the count is taken, so that check passes against the very
    code being replaced. Count constructions instead.
    """
    pe._run_with_timeout(lambda: 1, 2.0)          # ensure a worker already exists
    created = 0
    real_init = threading.Thread.__init__

    def counting_init(self, *args, **kwargs):
        nonlocal created
        created += 1
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(threading.Thread, "__init__", counting_init)
    for _ in range(50):
        assert pe._run_with_timeout(lambda: 1, 2.0) == 1
    assert created <= 1, (
        f"{created} threads constructed for 50 calls — each call is still spawning a "
        f"thread rather than reusing a persistent worker")


def test_each_thread_gets_its_own_worker_so_there_is_no_shared_lock():
    """R3: a shared queue would swap thread-creation contention for lock contention."""
    seen: list[object] = []
    lock = threading.Lock()

    def probe():
        pe._run_with_timeout(lambda: 1, 2.0)
        with lock:
            seen.append(getattr(pe._worker_tls, "worker", None))

    ts = [threading.Thread(target=probe) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    assert len(seen) == 4 and all(w is not None for w in seen)
    assert len({id(w) for w in seen}) == 4, "threads shared a worker — R3 violated"


def test_results_are_correct_under_concurrency():
    """Each caller must get ITS OWN result — never another thread's."""
    errors: list[str] = []

    def probe(n: int):
        for i in range(20):
            want = n * 1000 + i
            got = pe._run_with_timeout(lambda w=want: w, 2.0)
            if got != want:
                errors.append(f"thread {n} wanted {want}, got {got}")

    ts = [threading.Thread(target=probe, args=(n,)) for n in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    assert not errors, f"cross-thread result leakage: {errors[:5]}"
