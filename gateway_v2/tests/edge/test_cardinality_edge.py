# Feature: observability-contract
# Validates: Requirements 2.6, 3.4
"""Cardinality directory edge cases (task 4.4) — capacity-1 + existing-key update does not allocate.

Example/unit tests complementing the Property 1 / Property 3 loops: the two specific structural
edges the design calls out — a capacity-1 directory, and an existing-key update that never
allocates a new slot.
"""

from __future__ import annotations

from gateway_v2.edge.cardinality import CardinalityDirectory


def test_capacity_one_directory_overflows_after_first_slot() -> None:
    """A capacity-1 directory holds one tenant; the next distinct tenant overflows (R2.6)."""
    directory = CardinalityDirectory(_capacity=1, _k=1)
    directory.observe("alpha", 10)
    assert len(directory._slots) == 1
    assert directory.overflow_total() == 0

    # A second DISTINCT tenant cannot be allocated — size-check-before-insert routes to overflow.
    directory.observe("beta", 99)
    assert len(directory._slots) == 1, "capacity-1 directory grew past one slot"
    assert directory.overflow_total() == 1, "second distinct tenant did not overflow"
    assert "beta" not in directory._slots, "overflowed tenant allocated a slot"

    # The retained tenant's magnitude is untouched by the overflow.
    assert directory.top_k_series() == [("alpha", 10)]


def test_existing_key_update_does_not_allocate() -> None:
    """Updating an existing tenant's magnitude never allocates a new slot (R3.4)."""
    directory = CardinalityDirectory(_capacity=3, _k=3)
    directory.observe("x", 1)
    directory.observe("y", 2)
    size_before = len(directory._slots)
    assert size_before == 2

    # Re-observe an EXISTING key many times at a full-ish directory: no growth, no overflow.
    for magnitude in (5, 10, 15, 20):
        directory.observe("x", magnitude)
        assert len(directory._slots) == size_before, "existing-key update allocated a slot"
    assert directory.overflow_total() == 0, "existing-key update counted an overflow"
    # The in-place update is reflected.
    assert dict(directory.top_k_series())["x"] == 20
