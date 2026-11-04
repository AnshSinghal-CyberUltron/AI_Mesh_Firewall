"""GW29 mid-stream control sources (design 4.2, 5.8, 6.3).

An in-flight stream is cut at the next chunk boundary when the signed kill switch (global, org,
org-model or model scope) engages, when its API key is revoked, or when the snapshot is stale.
``InFlightControl.evaluate`` is unchanged: stale, max duration, kill, revoke, plan change.
This module supplies the :class:`~gateway_v2.edge.cancel.InFlightControlSource` it polls.

* :class:`KillSwitchControlSource` reads ONE :meth:`KillSwitchSnapshot.evaluate` (one lock, O(1), no
  I/O) and the revocation ledger. **Age semantics (C2).** ``InFlightControl`` compares
  ``snapshot.age_s`` to ``contract.max_snapshot_age_s()`` (about 20 ms) while the kill-switch
  snapshot may legitimately be ``FRESH_MS`` (5 000 ms) old, so passing the real refresh age would
  cut EVERY stream. The source therefore reports ``age_s = 0.0`` when the snapshot is not stale and
  ``available = False`` when it is: staleness is judged in exactly one place, the snapshot's own.
* :class:`RefusingControlSource` is the fail-closed default (no binding, no wiring): ``available``
  is  ``False``, so a stream cuts with ``STREAM_SNAPSHOT_STALE`` rather than running uncontrolled.
* :class:`DevNoControlSource` is the explicit dev/test source that never cuts; never a default.

A source that raises reports ``available = False`` (fail closed). Isolation does not cut a stream
(D2). Plan change is not wired (D11): :data:`PLAN_CHANGED_WIRED` is the named constant, ``False``.
"""

from __future__ import annotations

from typing import Protocol

from gateway_v2.admit.killswitch import KillSwitchSnapshot
from gateway_v2.admit.revocation import RevocationLedger
from gateway_v2.domain.incident import StreamBinding
from gateway_v2.edge.cancel import InFlightControlSource, InFlightSnapshot

__all__ = (
    "PLAN_CHANGED_WIRED",
    "DevNoControlSource",
    "InFlightControlFactory",
    "KillSwitchControlFactory",
    "KillSwitchControlSource",
    "RefusingControlSource",
)

#: Mid-stream plan-change detection is NOT wired by GW29 (design D11); the source always reports
#: ``plan_changed=False``. A test pins this so wiring it later is a deliberate edit.
PLAN_CHANGED_WIRED = False

#: ``age_s`` reported for a non-stale snapshot (C2): the snapshot's own judgement already decided.
_FRESH_AGE_S = 0.0


def _unavailable() -> InFlightSnapshot:
    return InFlightSnapshot(
        available=False,
        age_s=_FRESH_AGE_S,
        kill_switch_engaged=False,
        key_revoked=False,
        plan_changed=PLAN_CHANGED_WIRED,
    )


class InFlightControlFactory(Protocol):
    """Builds the per-stream source for one request's binding (``None`` = unwired)."""

    def for_stream(self, binding: StreamBinding | None) -> InFlightControlSource: ...


class RefusingControlSource:
    """Fail-closed default: no readable control plane, so the stream cuts (``available=False``)."""

    __slots__ = ()

    def snapshot(self) -> InFlightSnapshot:
        return _unavailable()


class DevNoControlSource:
    """Dev/test only: a fresh, available snapshot with every signal clear (never cuts)."""

    __slots__ = ()

    def snapshot(self) -> InFlightSnapshot:
        return InFlightSnapshot(
            available=True,
            age_s=_FRESH_AGE_S,
            kill_switch_engaged=False,
            key_revoked=False,
            plan_changed=PLAN_CHANGED_WIRED,
        )


class KillSwitchControlSource:
    """The production per-stream source bound to one ``(org, model, key, feed position)``."""

    __slots__ = ("_binding", "_ledger", "_switches")

    def __init__(
        self, switches: KillSwitchSnapshot, ledger: RevocationLedger, binding: StreamBinding
    ) -> None:
        self._switches = switches
        self._ledger = ledger
        self._binding = binding

    def snapshot(self) -> InFlightSnapshot:
        try:
            binding = self._binding
            evaluation = self._switches.evaluate(binding.org_id, binding.model)
            if evaluation.stale:
                return _unavailable()
            return InFlightSnapshot(
                available=True,
                age_s=_FRESH_AGE_S,
                kill_switch_engaged=evaluation.engaged,
                key_revoked=self._ledger.revoked_since(binding.key_hash, binding.key_feed_seq),
                plan_changed=PLAN_CHANGED_WIRED,
            )
        except Exception:  # noqa: BLE001 - a source that cannot read fails closed, never open.
            return _unavailable()


class KillSwitchControlFactory:
    """Production factory: one :class:`KillSwitchControlSource` per stream; ``None`` refuses."""

    __slots__ = ("_ledger", "_switches")

    def __init__(self, switches: KillSwitchSnapshot, ledger: RevocationLedger) -> None:
        self._switches = switches
        self._ledger = ledger

    def for_stream(self, binding: StreamBinding | None) -> InFlightControlSource:
        if binding is None:
            return RefusingControlSource()
        return KillSwitchControlSource(self._switches, self._ledger, binding)
