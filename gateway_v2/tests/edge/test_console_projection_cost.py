"""GW35: building the console is pure CPU. It opens no socket and reads only the ledger."""

from __future__ import annotations

import socket
import statistics
import time

import pytest

from gateway_v2.domain.decision import Decision, Disposition
from gateway_v2.edge.console_projection import console_from_holds
from gateway_v2.edge.request_holds import RequestHolds


def _holds() -> RequestHolds:
    holds = RequestHolds()
    holds.note_request("zs-cost-1")
    holds.note_identity_resolved()
    holds.note_plan("1.2.test")
    holds.note_input(
        Decision(
            disposition=Disposition.ALLOW, per_finding=(), transformations=(), findings=(),
            plan_version="1.2.test", deciding_rules=(), unavailable_detectors=(),
        )
    )
    holds.mark_dispatched()
    return holds


def test_console_from_holds_does_no_network_io(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("console_from_holds must not open a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    holds = _holds()
    samples = []
    for _ in range(200):
        start = time.perf_counter()
        assert console_from_holds(holds) is not None
        samples.append(time.perf_counter() - start)
    # Recorded, not asserted against a literal: the median is printed for the evidence file.
    print(f"console_from_holds median {statistics.median(samples) * 1e6:.1f} us over 200 calls")
