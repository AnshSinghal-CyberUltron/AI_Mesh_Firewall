"""Admission producer-only, label-free metrics tests (R2-07 / GW19), task 6.2.

Covers the pure ``gateway_v2.admit.metrics`` module only (producer side, no publisher):

* **Label-free series** (R7.3/R7.4, R2-10): the only dimension on any series is the fixed, finite
  ``queue`` name and the fixed, finite shed ``reason`` — never a tenant / org / owner label. An
  unknown queue name is rejected so no ad-hoc (and hence no tenant-derived) series can be created.
* **Real-zero counters** (R13.2): ``admitted_total``, every ``shed_total`` reason bucket, and the
  per-queue depth/age readings exist as ``0`` from the FIRST snapshot — "nothing happened" and "no
  series" are distinguishable to an alarm.
* **``fail_open_total`` pinned at 0** (R13.2): starts ``0`` and stays ``0`` across admits, sheds,
  and queue updates — the producer exposes no correct-operation increment path.
* **Depth/age readings reflect observations** (R7.3/R7.4): ``set_queue`` is reflected by the next
  snapshot; ``shed_total`` breaks down by the fixed reason set.
* **O(1) observe** (structural): the hot-path observers do not sort or scan — percentiles are
  computed on read inside ``snapshot``, never during an observation.

Test files are not under the import-linter layer contract.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from gateway_v2.admit import metrics as metrics_module
from gateway_v2.admit.metrics import (
    PREFIX,
    QUEUE_NAMES,
    AdmissionMetrics,
    QueueReport,
    ShedReason,
)

# --------------------------------------------------------------------------- #
# Label-free series: only the fixed queue + reason dimensions exist
# Validates: Requirements 7.3, 7.4, 13.2
# --------------------------------------------------------------------------- #


def test_prefix_and_fixed_queue_set() -> None:
    """The series prefix is ``amf_admit`` and the queue label set is fixed and finite."""
    assert PREFIX == "amf_admit"
    assert QUEUE_NAMES == ("request", "guard", "dispatch", "egress", "audit")


def test_shed_reasons_are_a_fixed_finite_set() -> None:
    """``shed_total`` is labelled only by the fixed reason enum (never a tenant value)."""
    assert {r.value for r in ShedReason} == {
        "admit",
        "backoff",
        "hard_cap",
        "queue_full",
        "undecidable",
    }


def test_no_tenant_label_on_any_series() -> None:
    """No series carries a tenant / org / owner label — the only labels are queue + reason.

    Structural proof: every per-queue reading key is a fixed queue name, every ``shed_total`` key
    is a fixed reason value, and there is no API accepting an owner/org/tenant identifier.
    """
    reading = AdmissionMetrics().snapshot()
    assert set(reading.queues.per_queue) == set(QUEUE_NAMES)
    assert set(reading.shed_total) == {r.value for r in ShedReason}
    # No producer method takes a tenant-like parameter.
    for name in ("observe_admit", "observe_shed", "set_queue", "snapshot"):
        params = set(inspect.signature(getattr(AdmissionMetrics, name)).parameters)
        assert not (params & {"owner", "owner_id", "org", "org_id", "tenant", "tenant_id"}), (
            f"{name} must not accept a tenant-derived label, got {params}"
        )


def test_unknown_queue_name_rejected() -> None:
    """An unknown queue name is rejected so no ad-hoc / tenant-derived series can be created."""
    producer = AdmissionMetrics()
    for bad in ("org-123", "tenant", "", "Request"):
        try:
            producer.set_queue(bad, depth=1, oldest_age_s=0.0)
        except KeyError:
            continue
        raise AssertionError(f"set_queue accepted an unknown queue name {bad!r}")


# --------------------------------------------------------------------------- #
# Real-zero counters from the first snapshot
# Validates: Requirements 7.3, 7.4, 13.2
# --------------------------------------------------------------------------- #


def test_counters_exist_as_zero_from_first_snapshot() -> None:
    """Before any observation every counter and every per-queue reading is a real ``0``."""
    reading = AdmissionMetrics().snapshot()
    assert reading.admitted_total == 0
    for reason in ShedReason:
        assert reading.shed_for(reason) == 0, f"{reason} bucket must exist as 0"
    for name in QUEUE_NAMES:
        assert reading.queues.depth(name) == 0
        assert reading.queues.oldest_age_s(name) == 0.0


def test_fail_open_total_starts_and_stays_zero() -> None:
    """``fail_open_total`` is pinned at 0 and no correct-operation path moves it (R13.2)."""
    producer = AdmissionMetrics()
    assert producer.snapshot().fail_open_total == 0
    producer.observe_admit()
    producer.observe_shed(ShedReason.BACKOFF)
    producer.observe_shed(ShedReason.UNDECIDABLE)
    producer.set_queue("request", depth=5, oldest_age_s=0.25)
    assert producer.snapshot().fail_open_total == 0
    # The producer exposes no public method that could increment a fail-open counter.
    assert not [n for n in dir(producer) if "fail_open" in n and callable(getattr(producer, n))]


# --------------------------------------------------------------------------- #
# Depth/age readings reflect observations; shed breakdown by reason
# Validates: Requirements 7.3, 7.4
# --------------------------------------------------------------------------- #


def test_depth_and_age_reflect_observations() -> None:
    """``set_queue`` is reflected by the next snapshot's per-queue depth + oldest age."""
    producer = AdmissionMetrics()
    producer.set_queue("request", depth=12, oldest_age_s=0.5)
    producer.set_queue("audit", depth=3, oldest_age_s=1.25)
    reading = producer.snapshot()
    assert reading.queues.depth("request") == 12
    assert reading.queues.oldest_age_s("request") == 0.5
    assert reading.queues.depth("audit") == 3
    assert reading.queues.oldest_age_s("audit") == 1.25
    # Untouched queues keep their real-zero reading.
    assert reading.queues.depth("guard") == 0
    assert reading.queues.oldest_age_s("egress") == 0.0


def test_set_queue_overwrites_latest_reading() -> None:
    """Depth/age are a current gauge: the latest ``set_queue`` wins, not an accumulation."""
    producer = AdmissionMetrics()
    producer.set_queue("dispatch", depth=4, oldest_age_s=0.1)
    producer.set_queue("dispatch", depth=1, oldest_age_s=0.02)
    reading = producer.snapshot()
    assert reading.queues.depth("dispatch") == 1
    assert reading.queues.oldest_age_s("dispatch") == 0.02


def test_admitted_and_shed_totals_count_observations() -> None:
    """``admitted_total`` and the per-reason ``shed_total`` buckets reflect observations."""
    producer = AdmissionMetrics()
    for _ in range(7):
        producer.observe_admit()
    producer.observe_shed(ShedReason.BACKOFF)
    producer.observe_shed(ShedReason.BACKOFF)
    producer.observe_shed(ShedReason.HARD_CAP)
    producer.observe_shed(ShedReason.QUEUE_FULL)
    producer.observe_shed(ShedReason.UNDECIDABLE)
    reading = producer.snapshot()
    assert reading.admitted_total == 7
    assert reading.shed_for(ShedReason.BACKOFF) == 2
    assert reading.shed_for(ShedReason.HARD_CAP) == 1
    assert reading.shed_for(ShedReason.QUEUE_FULL) == 1
    assert reading.shed_for(ShedReason.UNDECIDABLE) == 1
    assert reading.shed_for(ShedReason.ADMIT) == 0


def test_shed_total_breaks_down_by_the_fixed_reason_set() -> None:
    """``shed_total`` always carries exactly the fixed reason keys — no more, no fewer."""
    reading = AdmissionMetrics().snapshot()
    assert set(reading.shed_total) == {r.value for r in ShedReason}


# --------------------------------------------------------------------------- #
# Immutability of the frozen reading
# --------------------------------------------------------------------------- #


def test_reading_and_report_are_frozen() -> None:
    """``AdmissionReading`` and ``QueueReport`` are frozen; their maps cannot be mutated."""
    reading = AdmissionMetrics().snapshot()
    try:
        reading.admitted_total = 99  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("AdmissionReading must be frozen")
    # The per-queue mapping is a read-only proxy.
    try:
        reading.queues.per_queue["request"] = (1, 1.0)  # type: ignore[index]
    except TypeError:
        pass
    else:
        raise AssertionError("QueueReport.per_queue must be read-only")
    # The shed_total mapping is a read-only proxy.
    try:
        reading.shed_total["backoff"] = 1  # type: ignore[index]
    except TypeError:
        pass
    else:
        raise AssertionError("shed_total must be read-only")


def test_snapshot_does_not_drift_under_later_observations() -> None:
    """A snapshot is a stable copy: later observations do not mutate an already-taken reading."""
    producer = AdmissionMetrics()
    producer.observe_admit()
    first = producer.snapshot()
    producer.observe_admit()
    producer.observe_shed(ShedReason.BACKOFF)
    assert first.admitted_total == 1
    assert first.shed_for(ShedReason.BACKOFF) == 0


# --------------------------------------------------------------------------- #
# O(1) observe — structural: hot-path observers never sort or scan
# Validates: Requirements 7.3, 7.4
# --------------------------------------------------------------------------- #


def _assigned_names(fn: object) -> set[str]:
    """Names of functions called anywhere in ``fn``'s body (via AST)."""
    source = inspect.getsource(fn)  # type: ignore[arg-type]
    tree = ast.parse(_dedent(source))
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                called.add(func.id)
            elif isinstance(func, ast.Attribute):
                called.add(func.attr)
    return called


def _dedent(source: str) -> str:
    import textwrap

    return textwrap.dedent(source)


def test_observe_methods_are_o1_no_sort_or_scan() -> None:
    """The hot-path observers do not sort/scan — percentiles are computed on read in snapshot."""
    forbidden = {"sort", "sorted", "sum", "max", "min"}
    for name in ("observe_admit", "observe_shed", "set_queue"):
        called = _assigned_names(getattr(AdmissionMetrics, name))
        assert not (called & forbidden), (
            f"{name} must be O(1) (no {forbidden & called} on the hot path)"
        )


def test_queue_report_type_is_the_public_surface() -> None:
    """``QueueReport`` is the frozen read type exported by the module (producer surface)."""
    assert QueueReport.__module__ == metrics_module.__name__
    reading = AdmissionMetrics().snapshot()
    assert isinstance(reading.queues, QueueReport)


def test_lint_source_exists() -> None:
    """Sanity: the producer module ships in the admit package tree."""
    src = Path(inspect.getfile(metrics_module))
    assert src.name == "metrics.py"
    assert src.parent.name == "admit"
