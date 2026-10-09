"""The audit record as it is STORED: versioned, attributed to a tenant, and free of raw evidence.

`domain.DecisionRecord` is the decision. This is the thing that goes into a stream and then into
a durable sink, and it needs three properties the domain type does not have:

* **An org.** The memory budget, the trim, and the export are all per tenant. A record that
  cannot be attributed cannot be budgeted, so `org_id` is required rather than optional.
* **A schema version.** C3 requires the schema be versioned and consumed by GW14b. A durable
  sink outlives several gateway versions by design, so a reader has to be able to tell which
  shape it is holding without guessing from the keys present.
* **A stable serialization.** The per-org byte model sizes streams from the serialized length,
  so two equal records must produce equal bytes. `sort_keys` and fixed separators, and no
  floats where an int will do.

**What is deliberately NOT serialized: `Finding.evidence`.** It is the detector's matched text,
capped at 256 characters, and it is the one field in the decision that can carry the user's
secret or PII verbatim. An audit stream is append-only, is exported to a durable sink, and is
retained — so evidence in a record is a credential at rest in three places, and the redaction
the pipeline just performed would be undone by its own audit trail. Detector, version, category,
status, confidence and span OFFSETS are kept: offsets locate a finding without reproducing it.

GW14 owns the rest of the decision schema (detector and model hashes, the transformation
verification result, the per-stage status). This module carries what GW14c's budget, trim and
export need and stops there; `detail` is the seam those fields arrive through, so adding them
later is a schema bump rather than a rewrite.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from gateway_v2.domain.decision import DecisionRecord

SCHEMA_VERSION = 1
"""Bump when a field changes meaning or disappears. Additions do not need it; GW14b reads by
name and a reader that finds an unexpected version must say so rather than interpret it."""


class AuditPhase(StrEnum):
    """Which point in the lifecycle produced the record. One record per phase (C3)."""

    INPUT = "input"
    OUTPUT = "output"
    ADMISSION = "admission"
    """Admission decisions, including the 503 sheds C40 requires a record for."""


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """One stored audit row. Immutable, self-describing, and carrying no raw evidence."""

    request_id: str
    org_id: str
    phase: AuditPhase
    outcome: str
    """The disposition (`allow` / `flag` / `redact` / `block`) or an admission outcome."""

    recorded_at_ns: int
    detail: Mapping[str, object] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.request_id:
            raise ValueError("an audit record needs a request_id, or no join can resolve it")
        if not self.org_id:
            raise ValueError(
                "an audit record needs an org_id: an unattributed record cannot be budgeted, "
                "trimmed per tenant, or exported to the right tenant's sink",
            )
        if not self.outcome:
            raise ValueError("an audit record needs an outcome; 'unknown' is an outcome")
        if self.recorded_at_ns < 0:
            raise ValueError("recorded_at_ns must not be negative")

    def as_mapping(self) -> Mapping[str, object]:
        """The serializable shape. Flat, and ordered by the serializer rather than by luck."""
        return {
            "v": self.schema_version,
            "request_id": self.request_id,
            "org_id": self.org_id,
            "phase": self.phase.value,
            "outcome": self.outcome,
            "at_ns": self.recorded_at_ns,
            "detail": dict(self.detail),
        }

    def serialize(self) -> bytes:
        """Stable bytes. Equal records serialize equally, which is what the byte model assumes."""
        return json.dumps(
            self.as_mapping(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")


def finding_detail(record: DecisionRecord) -> list[Mapping[str, object]]:
    """Every finding, including SKIPPED and UNAVAILABLE (C3), and never its evidence.

    Absence is a status here, not a missing entry: the prototype reported five of seven stages as
    executed unconditionally, and a findings list that silently omits what did not run reproduces
    exactly that.
    """
    return [
        {
            "detector": finding.detector,
            "detector_version": finding.detector_version,
            "category": str(finding.category),
            "status": finding.status.value,
            "confidence": finding.confidence,
            "spans": [[span.start, span.end] for span in finding.spans],
        }
        for finding in record.decision.findings
    ]


def from_decision(
    record: DecisionRecord,
    *,
    org_id: str,
    phase: AuditPhase,
    recorded_at_ns: int,
) -> AuditRecord:
    """Build the stored record from a decision. The ONE place the mapping is defined."""
    decision = record.decision
    return AuditRecord(
        request_id=record.request_id,
        org_id=org_id,
        phase=phase,
        outcome=decision.disposition.value,
        recorded_at_ns=recorded_at_ns,
        detail={
            "plan_version": record.plan_version,
            "deciding_rules": list(decision.deciding_rules),
            "unavailable_detectors": list(decision.unavailable_detectors),
            "per_finding": [
                {"detector": item.detector, "disposition": item.disposition.value}
                for item in decision.per_finding
            ],
            "transformations": [
                {
                    "kind": change.kind,
                    "span": [change.span.start, change.span.end],
                    "replacement": change.replacement,
                }
                for change in decision.transformations
            ],
            "findings": finding_detail(record),
        },
    )


# --- C40: sheds and admission rejects get records too --------------------------------------------

SHED = "shed"
"""Admitted, then answered with a declared 503 under overload rather than served."""

REJECTED = "rejected"
"""Refused at admission: a kill switch, a revoked key, an unverifiable plan."""


def admission(
    *,
    request_id: str,
    org_id: str,
    outcome: str,
    reason: str,
    recorded_at_ns: int,
    status: int,
) -> AuditRecord:
    """A record for an admission decision, including a 503 shed (C40).

    C40 is a counting rule as much as a recording one: *"every admitted request, including 503
    sheds, gets a record and completeness is measured against ADMITTED requests"*. The prototype's
    ratio read 1.0 while up to 5.75% of admitted requests were shed without a record — a denominator
    of "requests we managed to serve" cannot show that, because the missing records are missing
    from both halves of the fraction at once.

    So a shed is not an absence of a decision. It is a decision, with a reason, and it is audited
    through the same producer, the same budget and the same durable sink as a served request.
    """
    if not reason:
        raise ValueError(
            "an admission record needs a reason: `rejected_503` merging every cause is R2-11's "
            "M2 finding (9,241 kill-switch 503s indistinguishable from 8 store-outage 503s)",
        )
    return AuditRecord(
        request_id=request_id,
        org_id=org_id,
        phase=AuditPhase.ADMISSION,
        outcome=outcome,
        recorded_at_ns=recorded_at_ns,
        detail={"reason": reason, "status": status},
    )


def shed(
    *,
    request_id: str,
    org_id: str,
    reason: str,
    recorded_at_ns: int,
    retry_after_ms: int,
) -> AuditRecord:
    """A declared 503 shed under overload. Admitted, counted, answered late or refused — audited.

    `retry_after_ms` is carried because R2-08 measured sheds going out with 6-11 ms and the
    OpenAI SDK honouring it into an immediate retry, so the value is part of what the shed DID.
    """
    record = admission(
        request_id=request_id,
        org_id=org_id,
        outcome=SHED,
        reason=reason,
        recorded_at_ns=recorded_at_ns,
        status=503,
    )
    return AuditRecord(
        request_id=record.request_id,
        org_id=record.org_id,
        phase=record.phase,
        outcome=record.outcome,
        recorded_at_ns=record.recorded_at_ns,
        detail={**record.detail, "retry_after_ms": retry_after_ms},
    )
