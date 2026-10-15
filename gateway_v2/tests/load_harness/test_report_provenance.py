"""Reconciliation unit tests for the additive ``RunProvenance`` edit (B7 / R2-20), task 7.4.

Representative EXAMPLES (NOT property-based tests — no >= 10,000-iteration loop) that guard the
additive, non-breaking edit to the shipped B6 ``load_harness.report`` module: the ``RunReport``
frozen dataclass gained ONE optional ``provenance: RunProvenance | None = None`` field, and a new
``RunProvenance`` frozen+slots value type was introduced (Req 5.1, 5.4, 6.2).

The point of this file is attribution without breakage:

* a ``RunReport`` constructed WITHOUT ``provenance`` (the shipped default path) still constructs and
  reads back ``provenance is None`` — proving the additive edit preserved the existing 15-arg
  construction signature every shipped site and test depends on (Req 6.2 non-breaking).
* a ``RunReport`` built WITH a ``RunProvenance`` round-trips every shipped field unchanged PLUS
  carries the provenance — reconcile, not rebuild (Req 5.4): the report is the run's output and the
  provenance travels on it rather than in a parallel structure.
* ``RunProvenance`` is a frozen+slots value: immutable (``FrozenInstanceError`` on attribute set)
  and its three fields read back as set (Req 6.2).
* a field-count / field-name assertion over ``RunReport.__dataclass_fields__`` so a future field
  reorder or drop of a shipped field is caught (15 original + ``provenance`` = 16).

These are plain-``assert`` examples; the file imports nothing beyond the module under test and the
value types it carries. Test files are not under the import-linter layer contract.
"""

from __future__ import annotations

import math
from dataclasses import FrozenInstanceError

import pytest

from load_harness.report import GpuBusy, ReasonMix, RunProvenance, RunReport

# The 15 shipped ``RunReport`` fields, in declaration order, that predate the B7 additive edit. The
# 16th field is the new optional ``provenance``. Pinned here so a future reorder/drop is caught.
_SHIPPED_FIELDS: tuple[str, ...] = (
    "c4_p99_ms",
    "slo_bound_ms",
    "c4_passes",
    "owner_queue_wait_s",
    "event_loop_lag_s",
    "reason_mix",
    "schedule_drop_count",
    "loadgen_headroom",
    "generator_bound",
    "realized_strata_shares",
    "distinct_keys",
    "distinct_orgs",
    "distinct_tenants",
    "gpu_busy",
    "arrival_mode_constant",
)

_DIGEST = "0" * 64  # a stand-in aggregate SHA-256 hex digest (release-identity, not a measurement)


def _reason_mix() -> ReasonMix:
    return ReasonMix.from_counts({"admitted": 7, "overload_shed": 3})


def _shipped_kwargs() -> dict[str, object]:
    """The 15 shipped fields as explicit kwargs — the pre-B7 construction signature verbatim."""
    return {
        "c4_p99_ms": 5.0,
        "slo_bound_ms": 20.0,
        "c4_passes": True,
        "owner_queue_wait_s": 0.01,
        "event_loop_lag_s": 0.002,
        "reason_mix": _reason_mix(),
        "schedule_drop_count": 0,
        "loadgen_headroom": 0.5,
        "generator_bound": False,
        "realized_strata_shares": (("long_prompt", 0.05),),
        "distinct_keys": 2_000,
        "distinct_orgs": 200,
        "distinct_tenants": 10_000,
        "gpu_busy": GpuBusy.UNAVAILABLE,
        "arrival_mode_constant": False,
    }


# --------------------------------------------------------------------------------------------------
# The additive edit preserved the shipped default path — a report WITHOUT provenance (Req 6.2).
# --------------------------------------------------------------------------------------------------


def test_run_report_without_provenance_still_constructs_and_is_none() -> None:
    """The shipped 15-arg construction path still works and ``provenance`` defaults to ``None``.

    This is the non-breaking guarantee: every existing construction site/test that does not supply
    provenance keeps compiling and passing (Req 6.2) and reads back ``provenance is None``.
    """
    report = RunReport(**_shipped_kwargs())  # type: ignore[arg-type]
    assert report.provenance is None
    # the shipped fields read back exactly as supplied — the default path is untouched
    assert report.c4_p99_ms == 5.0
    assert report.c4_passes is True
    assert report.gpu_busy is GpuBusy.UNAVAILABLE
    assert report.reason_mix.total == 10


# --------------------------------------------------------------------------------------------------
# Reconcile, not rebuild — a report WITH provenance round-trips every shipped field (Req 5.4).
# --------------------------------------------------------------------------------------------------


def test_run_report_with_provenance_round_trips_every_shipped_field() -> None:
    """A report built with all 15 shipped fields + a ``RunProvenance`` carries both unchanged.

    Reconcile, not rebuild (Req 5.4): the provenance travels on the existing ``RunReport`` rather
    than in a parallel report structure, and no shipped field is perturbed by its presence.
    """
    provenance = RunProvenance(
        harness_version="v2.3",
        aggregate_sha256=_DIGEST,
        integrity_intact=True,
    )
    report = RunReport(**_shipped_kwargs(), provenance=provenance)  # type: ignore[arg-type]

    # every original field reads back exactly as set
    assert report.c4_p99_ms == 5.0
    assert report.slo_bound_ms == 20.0
    assert report.c4_passes is True
    assert report.owner_queue_wait_s == 0.01
    assert report.event_loop_lag_s == 0.002
    assert report.reason_mix.as_mapping()["admitted"] == 7
    assert report.schedule_drop_count == 0
    assert report.loadgen_headroom == 0.5
    assert report.generator_bound is False
    assert report.realized_strata_shares == (("long_prompt", 0.05),)
    assert report.distinct_keys == 2_000
    assert report.distinct_orgs == 200
    assert report.distinct_tenants == 10_000
    assert report.gpu_busy is GpuBusy.UNAVAILABLE
    assert report.arrival_mode_constant is False

    # AND it now carries the provenance of the exact release that produced it
    assert report.provenance is provenance
    assert report.provenance.harness_version == "v2.3"
    assert report.provenance.aggregate_sha256 == _DIGEST
    assert report.provenance.integrity_intact is True


def test_run_report_with_provenance_does_not_disturb_sentinel_or_infinite_p99() -> None:
    """Attaching provenance leaves the GPU-busy sentinel and an all-errored +inf p99 intact."""
    errored = dict(_shipped_kwargs())
    errored.update(c4_p99_ms=math.inf, c4_passes=False)
    report = RunReport(
        **errored,  # type: ignore[arg-type]
        provenance=RunProvenance(
            harness_version="v2.3", aggregate_sha256=_DIGEST, integrity_intact=False
        ),
    )
    assert math.isinf(report.c4_p99_ms)
    assert report.c4_passes is False
    assert report.gpu_busy is GpuBusy.UNAVAILABLE
    # a non-attributable (mutated) release records integrity_intact=False honestly
    assert report.provenance is not None
    assert report.provenance.integrity_intact is False


# --------------------------------------------------------------------------------------------------
# RunProvenance is a frozen+slots immutable value (Req 6.2).
# --------------------------------------------------------------------------------------------------


def test_run_provenance_fields_read_back() -> None:
    """The three ``RunProvenance`` fields read back exactly as constructed."""
    provenance = RunProvenance(
        harness_version="v2.3",
        aggregate_sha256=_DIGEST,
        integrity_intact=True,
    )
    assert provenance.harness_version == "v2.3"
    assert provenance.aggregate_sha256 == _DIGEST
    assert provenance.integrity_intact is True


def test_run_provenance_is_frozen() -> None:
    """A frozen dataclass rejects attribute assignment — provenance is immutable once built."""
    provenance = RunProvenance(
        harness_version="v2.3",
        aggregate_sha256=_DIGEST,
        integrity_intact=True,
    )
    with pytest.raises(FrozenInstanceError):
        provenance.harness_version = "v9.9"  # type: ignore[misc]


def test_run_provenance_has_slots() -> None:
    """``slots=True`` means no ``__dict__`` and a new attribute cannot be set."""
    provenance = RunProvenance(
        harness_version="v2.3",
        aggregate_sha256=_DIGEST,
        integrity_intact=True,
    )
    assert not hasattr(provenance, "__dict__")
    # a frozen+slots dataclass rejects an unknown attribute: the frozen ``__setattr__`` guard fires
    # (raising rather than ever creating the attribute, since there is no ``__dict__`` slot for it).
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        provenance.unexpected = "x"  # type: ignore[attr-defined]


# --------------------------------------------------------------------------------------------------
# Field-shape sanity — 15 shipped fields + provenance = 16, order preserved (reorder/drop caught).
# --------------------------------------------------------------------------------------------------


def test_run_report_field_shape_is_shipped_plus_provenance() -> None:
    """``RunReport`` has exactly the 15 shipped fields (in order) followed by ``provenance``.

    Guards a future reorder or drop of a shipped field: the shipped prefix must match byte-for-byte
    and the only addition is the optional ``provenance`` field at the end.
    """
    fields = tuple(RunReport.__dataclass_fields__)
    assert len(fields) == 16
    assert fields[:15] == _SHIPPED_FIELDS
    assert fields[15] == "provenance"
    # the new field is optional (has a default) so the shipped construction signature is preserved
    assert RunReport.__dataclass_fields__["provenance"].default is None
