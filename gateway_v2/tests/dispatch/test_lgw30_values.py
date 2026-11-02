"""LGW30 adapter value-type unit tests (GW30, task 4.2).

Pins the **absent (``None``) vs empty (``()``) invariant** and the frozen /
hashable contract of the ``dispatch/vector_adapter`` value types (task 4.1):

* A zero-record success — ``AdapterResult(posture.VECTOR_OK, records=())`` — is
  PRESENT but empty: ``ok is True`` and ``records == ()`` (R1.6, R5.4, R5.5).
* Any non-OK code with ``records is None`` is ABSENT: ``ok is False`` (R6.6).
* The two are never equal, so success-vs-failure is distinguishable by value
  alone (R1.7, R5.4).
* ``Capabilities`` leaves no flag undefined — all three booleans are always
  present on every instance (R3.1).
* Every request value type is frozen (field assignment raises) and hashable
  (R1.6, R6.6 — frozen slotted dataclasses).

These are supporting unit tests (concrete examples / boundaries), the companion
to the seeded property tests; no ``hypothesis``.

_Design: Data Models (absent-vs-empty invariant)._
_Requirements: 1.6, 1.7, 3.1, 5.4, 5.5, 6.6._
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from types import MappingProxyType

import pytest

from gateway_v2.dispatch.vector_adapter import (
    AdapterResult,
    Capabilities,
    CreateRequest,
    DeleteRequest,
    HealthRequest,
    ListRequest,
    QueryRequest,
    UpsertRequest,
    VectorRecord,
)
from gateway_v2.domain import posture

# Every GW30 non-OK posture code: a result carrying any of these must be ABSENT
# (``records is None``) and distinguishable from a zero-record success.
_NON_OK_CODES = (
    posture.UNSUPPORTED_OPERATION,
    posture.PROVIDER_UNREACHABLE,
    posture.CREDENTIAL_UNAVAILABLE,
    posture.STORE_NOT_CONFIGURED,
    posture.CREDENTIAL_SCOPE_MISMATCH,
    posture.ORG_IDENTITY_INVALID,
    posture.VECTOR_CAPACITY_EXCEEDED,
    posture.VECTOR_CAPACITY_UNSET,
    posture.CREDENTIAL_LEAK_WITHHELD,
)


def _record() -> VectorRecord:
    """A minimal immutable record for the hashability / payload fixtures."""
    return VectorRecord(id="r1", vector=(0.0, 1.0), metadata=MappingProxyType({"k": "v"}))


# --------------------------------------------------------------------------- #
# Present-but-empty success.
# --------------------------------------------------------------------------- #


def test_zero_record_success_is_present_empty_and_ok() -> None:
    """``AdapterResult(VECTOR_OK, records=())`` is a PRESENT, empty success (R1.6, R5.4, R5.5)."""
    result = AdapterResult(posture.VECTOR_OK, records=())
    assert result.ok is True
    assert result.records == ()
    assert result.records is not None  # present, not absent


# --------------------------------------------------------------------------- #
# Absent failure.
# --------------------------------------------------------------------------- #


def test_non_ok_code_with_records_none_is_absent_and_not_ok() -> None:
    """Any non-OK code with ``records is None`` is ABSENT and not a success (R6.6)."""
    for code in _NON_OK_CODES:
        result = AdapterResult(code, records=None)
        assert result.ok is False, f"{code!r} unexpectedly reported ok"
        assert result.records is None, f"{code!r} should carry absent (None) records"


# --------------------------------------------------------------------------- #
# None (absent) vs () (empty) are never interchangeable.
# --------------------------------------------------------------------------- #


def test_empty_success_is_not_equal_to_absent_failure() -> None:
    """A present-empty success and an absent failure are never equal (R1.7, R5.4)."""
    empty_success = AdapterResult(posture.VECTOR_OK, records=())
    for code in _NON_OK_CODES:
        absent_failure = AdapterResult(code, records=None)
        assert empty_success != absent_failure, (
            f"zero-record success was indistinguishable from {code!r}"
        )
        # The distinction holds by ``ok`` alone (code-driven), not by eye.
        assert empty_success.ok is not absent_failure.ok


def test_none_and_empty_tuple_records_are_distinct_results() -> None:
    """``records=None`` and ``records=()`` under the same code are unequal (R5.5)."""
    absent = AdapterResult(posture.VECTOR_OK, records=None)
    empty = AdapterResult(posture.VECTOR_OK, records=())
    assert absent != empty
    assert absent.records is None
    assert empty.records == ()


# --------------------------------------------------------------------------- #
# Capabilities leaves no flag undefined.
# --------------------------------------------------------------------------- #


def test_capabilities_has_all_three_flags_defined() -> None:
    """Every ``Capabilities`` instance carries all three booleans (R3.1)."""
    for filters in (False, True):
        for namespaces in (False, True):
            for hybrid_search in (False, True):
                caps = Capabilities(
                    filters=filters,
                    namespaces=namespaces,
                    hybrid_search=hybrid_search,
                )
                assert caps.filters is filters
                assert caps.namespaces is namespaces
                assert caps.hybrid_search is hybrid_search
                # No flag is ever absent: the slots are exactly the three bools.
                for flag in ("filters", "namespaces", "hybrid_search"):
                    assert isinstance(getattr(caps, flag), bool)


def test_capabilities_requires_every_flag() -> None:
    """A ``Capabilities`` cannot be built with a flag omitted (no undefined flag, R3.1)."""
    with pytest.raises(TypeError):
        Capabilities(filters=True, namespaces=True)  # type: ignore[call-arg]


# --------------------------------------------------------------------------- #
# Request values are frozen and hashable.
# --------------------------------------------------------------------------- #


def _request_values() -> tuple[object, ...]:
    """One instance of each request value type plus the shared value types."""
    rec = _record()
    return (
        QueryRequest(
            org_id="org-a",
            store_id="pinecone",
            collection="c1",
            vector=(0.1, 0.2),
            top_k=5,
        ),
        UpsertRequest(org_id="org-a", store_id="pinecone", collection="c1", records=(rec,)),
        DeleteRequest(org_id="org-a", store_id="pinecone", collection="c1", ids=("r1",)),
        ListRequest(org_id="org-a", store_id="pinecone"),
        CreateRequest(
            org_id="org-a",
            store_id="pinecone",
            collection="c1",
            dimension=2,
            metric="cosine",
        ),
        HealthRequest(org_id="org-a", store_id="pinecone"),
        AdapterResult(posture.VECTOR_OK, records=()),
        Capabilities(filters=True, namespaces=False, hybrid_search=False),
        rec,
    )


def test_request_values_are_frozen() -> None:
    """Field assignment on any value raises ``FrozenInstanceError`` (frozen dataclasses, R1.6).

    Each value type has a different first field (the request types carry
    ``org_id``, ``AdapterResult`` carries ``code``, ``Capabilities`` carries
    ``filters``, ``VectorRecord`` carries ``id``), so the assignment targets an
    existing field via ``dataclasses.fields`` — assigning to a *non-existent*
    attribute on a ``slots=True`` dataclass raises ``TypeError`` from the slots
    machinery, which would not prove the frozen contract.
    """
    for value in _request_values():
        field_name = fields(value)[0].name
        with pytest.raises(FrozenInstanceError):
            setattr(value, field_name, "mutated")


def test_request_values_are_hashable() -> None:
    """Every request value is hashable (frozen slotted dataclasses, R6.6)."""
    for value in _request_values():
        # hash() must not raise; the value is usable as a set / dict key.
        assert hash(value) == hash(value)
        assert value in {value}
