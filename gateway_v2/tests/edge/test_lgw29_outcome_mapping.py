"""GW29 `outcome_for`: the closed table of design 5.5. A tenant cannot trip an isolation."""
from __future__ import annotations

import pytest

from gateway_v2.domain.incident import ModelOutcome
from gateway_v2.edge.incident_wiring import outcome_for


@pytest.mark.parametrize(("status", "expected"), [
    (500, ModelOutcome.SERVER_ERROR),
    (502, ModelOutcome.SERVER_ERROR),
    (503, ModelOutcome.SERVER_ERROR),
    (504, ModelOutcome.SERVER_ERROR),
    (529, ModelOutcome.SERVER_ERROR),
    (599, ModelOutcome.SERVER_ERROR),
    (429, ModelOutcome.RATE_LIMITED),
])
def test_countable_provider_statuses(status: int, expected: ModelOutcome) -> None:
    assert outcome_for(status=status) is expected


@pytest.mark.parametrize("status", [400, 401, 403, 404, 408, 413, 422, 418, 301, 600, 200, 0])
def test_client_attributable_and_unmapped_statuses_count_for_nothing(status: int) -> None:
    assert outcome_for(status=status) is None, status


def test_non_status_facts() -> None:
    assert outcome_for(timed_out=True) is ModelOutcome.TIMEOUT
    assert outcome_for(malformed=True) is ModelOutcome.MALFORMED
    assert outcome_for(guard_hit=True) is ModelOutcome.GUARD_HIT
    assert outcome_for(completed=True) is ModelOutcome.OK


def test_nothing_known_counts_for_nothing() -> None:
    assert outcome_for() is None, "gateway-side refusals, disconnects and drain are not attempts"


def test_precedence_is_fixed() -> None:
    assert outcome_for(status=429, timed_out=True) is ModelOutcome.TIMEOUT
    assert outcome_for(status=500, malformed=True) is ModelOutcome.MALFORMED
    assert outcome_for(status=500, guard_hit=True) is ModelOutcome.GUARD_HIT
    assert outcome_for(status=400, completed=True) is None, "a client error is never an OK sample"
