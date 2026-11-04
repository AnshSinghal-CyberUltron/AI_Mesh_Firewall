# Validates: AC8 (holds carry the incident facts; still a fixed slotted cell)
"""``RequestHolds`` gained incident slots; it must stay a ``__dict__``-free fixed cell."""

from __future__ import annotations

import pytest

from gateway_v2.domain.incident import RerouteNote
from gateway_v2.edge.request_holds import RequestHolds


def test_still_has_no_instance_dict() -> None:
    holds = RequestHolds()
    assert not hasattr(holds, "__dict__")
    with pytest.raises(AttributeError):
        holds.surprise = 1  # type: ignore[attr-defined]


def test_incident_notes_round_trip_and_default_to_none() -> None:
    holds = RequestHolds()
    assert (holds.incident_cause, holds.incident_underlying, holds.reroute) == (None, None, None)
    assert holds.incident_retry_after_s is None
    holds.note_incident_cause("recheck", underlying="model_killed", retry_after_s=1.5)
    assert (holds.incident_cause, holds.incident_underlying) == ("recheck", "model_killed")
    assert holds.incident_retry_after_s == 1.5
    note = RerouteNote("a", "b", "a", "isolation", "isolated_fallback")
    holds.note_reroute(note)
    assert holds.reroute is note


def test_a_refused_request_is_refunded_exactly_once_because_it_is_undispatched() -> None:
    class Quota:
        refunds: list[int] = []

        def refund(self, cost: int, *, request_id: str, owner: str) -> None:
            self.refunds.append(cost)

    class Admission:
        def complete(self, request_id: str) -> None:
            return None

    holds = RequestHolds()
    holds.note_tenant("o")
    holds.admitted("r")
    holds.spent(5, "r")
    holds.note_incident_cause("recheck", underlying="kill_switch_engaged", retry_after_s=2.0)
    quota = Quota()
    holds.release(Admission(), quota)  # type: ignore[arg-type]
    holds.release(Admission(), quota)  # type: ignore[arg-type]
    assert quota.refunds == [5]
