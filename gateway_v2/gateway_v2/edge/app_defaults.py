"""Fail-safe defaults for `build_app` when a deployment has not wired a component.

Split out of `edge/app.py` (800 lines, the size gate) in GW35. Behaviour is unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence

from gateway_v2.domain import Finding
from gateway_v2.edge.cancel import InFlightSnapshot

__all__ = ("NoControlSource", "no_error_findings", "one_stream")


def no_error_findings(_frame: str) -> Sequence[Finding]:
    """The default error-frame findings scanner when GW07/GW08's detector is not yet wired.

    Returns NO findings, so a mid-stream error frame resolves to ALLOW and is forwarded unchanged
    (R11.2) — the same thin-handler posture as the content path, which runs the shipped pipeline
    with ``detector=None`` (no findings) until GW15 wires the real detector. This is NOT a
    fail-open hole: a withhold only happens on a BLOCK decision or a scan error (R11.3/R11.4), and
    the default scanner never raises; a deployment that must guard error frames injects the real
    findings detector through :func:`build_app`'s ``error_scanner`` (above ``detect``), at which
    point a secret in an error frame is redacted/blocked exactly as in released content.
    """
    return ()


def one_stream() -> int:
    """The default live-concurrency count when GW19's counter is not yet wired.

    One active stream — so the coalescer's derived per-stream high-water is computed for a single
    stream. Not a capacity literal: it is a count of in-flight streams, the quantity the live
    counter reports, not a configured bound.
    """
    return 1


class NoControlSource:
    """The default in-flight control source when the control-plane feed is not yet wired.

    Reports a FRESH, AVAILABLE snapshot with every signal clear, so a stream runs under the
    max-duration deadline alone until GW05/GW06 inject the real kill-switch / revocation / plan
    feed. ``age_s=0.0`` keeps it inside any derived ``max_snapshot_age_s()`` (so it is not treated
    as stale), and ``available=True`` means it does not trip the fail-closed path for a deployment
    that simply has not wired the control plane yet. A real source replaces this with the signed
    snapshot read; the max-stream-duration trigger is active regardless, since that bound is purely
    the injected clock vs the contract.
    """

    __slots__ = ()

    def snapshot(self) -> InFlightSnapshot:
        return InFlightSnapshot(
            available=True,
            age_s=0.0,
            kill_switch_engaged=False,
            key_revoked=False,
            plan_changed=False,
        )
