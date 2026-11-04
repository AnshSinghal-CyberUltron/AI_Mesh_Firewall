# Validates: Requirements 6 (lock order: IncidentState lock I and snapshot lock S are never nested)
"""Concurrent `sweep` / reads / `_absorb` under a watchdog, plus an AST nesting check."""

from __future__ import annotations

import ast
import inspect
import threading

from gateway_v2.admit import incident_state
from tests.admit._lgw29_support import Clock, iso_body, ks, load, local_auto, make_state, snapshot


def test_no_lock_body_in_incident_state_touches_the_snapshot() -> None:
    tree = ast.parse(inspect.getsource(incident_state))
    offenders: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        for item in node.items:
            expr = item.context_expr
            if (isinstance(expr, ast.Attribute) and expr.attr == "_lock"
                    and isinstance(expr.value, ast.Name) and expr.value.id == "self"):
                for inner in ast.walk(ast.Module(body=node.body, type_ignores=[])):
                    if isinstance(inner, ast.Attribute) and inner.attr == "_switches":
                        offenders.append(inner.lineno)
    assert not offenders, f"self._switches used inside `with self._lock` at lines {offenders}"


def test_sweep_reads_and_absorb_run_concurrently_without_deadlock() -> None:
    wall, mono = Clock(1_000.0), Clock(100.0)
    snap = snapshot(mono)
    state = make_state(snap, wall, mono, slots=8)
    errors: list[BaseException] = []
    stop = threading.Event()

    def guarded(fn):  # type: ignore[no-untyped-def]
        def run() -> None:
            try:
                while not stop.is_set():
                    fn()
            except BaseException as exc:  # noqa: BLE001 - surface any failure to the main thread
                errors.append(exc)
        return run

    seq = iter(range(1, 10_000_000))

    def absorb() -> None:
        n = next(seq)
        on = n % 2 == 0
        load(snap, [ks("iso:m", on=on, body=iso_body(origin="auto", expires_at="x") if on else None,
                       feed_seq=n)])

    def reads() -> None:
        state.active_isolation("m")
        state.snapshot_models()
        state.expired_signed_count()

    def locals_() -> None:
        state.add_local(local_auto("n", wall.now, dur=1.0))
        wall.now += 0.5

    threads = [threading.Thread(target=guarded(f), daemon=True)
               for f in (absorb, reads, reads, state.sweep, locals_)]
    for t in threads:
        t.start()
    stop.wait(0.6)
    stop.set()
    for t in threads:
        t.join(timeout=2.0)  # the 2 s watchdog: a deadlock leaves a thread alive
    assert not any(t.is_alive() for t in threads), "deadlock between lock I and lock S"
    assert not errors, errors
