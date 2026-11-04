"""GW29 incident audit records for a request, split out of ``edge/audit_emit.py`` (design 6.4a).

Rules (one record per refusal, one terminal record per cut):

* a REFUSAL (``holds.incident_cause`` set and not a ``cut_*``) writes exactly ONE record, the
  ``incident_refusal``; the generic input/output/admission records are suppressed, including for a
  recheck refusal where an input decision already exists;
* a CUT (``cut_*``) replaces the generic OUTPUT terminal record with one ``incident_cut`` record;
* a REROUTE adds the routing provenance to the request's records and one ``incident_reroute``.

Every function is pure and returns records; the caller enqueues them. No content, key or
credential reaches a record: the model names are operator-known and the org id is the record's own.
"""

from __future__ import annotations

import math
from types import MappingProxyType

from gateway_v2.audit.record import (
    AuditRecord,
    incident_cut,
    incident_refusal,
    incident_reroute,
)
from gateway_v2.edge import errors
from gateway_v2.edge.request_holds import RequestHolds

__all__ = (
    "effective_refuse_code",
    "incident_is_cut",
    "incident_is_refusal",
    "incident_records",
    "reroute_record",
    "with_routing",
)

_RECHECK = "recheck"
_CUT_PREFIX = "cut_"
_MS_PER_S = 1000

#: Stable name of the ``detail`` key the reroute provenance is copied under.
ROUTING_KEY = "routing"


def incident_is_cut(holds: RequestHolds) -> bool:
    cause = holds.incident_cause
    return cause is not None and cause.startswith(_CUT_PREFIX)


def incident_is_refusal(holds: RequestHolds) -> bool:
    cause = holds.incident_cause
    return cause is not None and not cause.startswith(_CUT_PREFIX)


def effective_refuse_code(holds: RequestHolds, refuse_code: str | None) -> str | None:
    """The refused code: the caller's, or the recheck's underlying one when the caller has none."""
    if refuse_code is None and holds.incident_cause == _RECHECK:
        return holds.incident_underlying
    return refuse_code


def incident_records(
    holds: RequestHolds, refuse_code: str | None, recorded_at_ns: int
) -> list[AuditRecord]:
    """The incident records for this request: a refusal, a cut, or none (``[]``)."""
    cause = holds.incident_cause
    if cause is None:
        return []
    if incident_is_cut(holds):
        return [
            incident_cut(
                request_id=holds.request_id,
                org_id=holds.org_id,
                code=holds.incident_underlying or cause,
                cause=cause,
                recorded_at_ns=recorded_at_ns,
            )
        ]
    code = effective_refuse_code(holds, refuse_code)
    if code is None:
        return []
    retry = holds.incident_retry_after_s
    retry_ms = 0 if retry is None or not math.isfinite(retry) else math.ceil(retry * _MS_PER_S)
    status = errors.render_http(code, request_id=holds.request_id).status
    return [
        incident_refusal(
            request_id=holds.request_id,
            org_id=holds.org_id,
            code=code,
            cause=cause,
            recorded_at_ns=recorded_at_ns,
            retry_after_ms=retry_ms,
            status=status,
            underlying=holds.incident_underlying if cause == _RECHECK else None,
        )
    ]


def reroute_record(holds: RequestHolds, recorded_at_ns: int) -> AuditRecord | None:
    """The one ``incident_reroute`` record, or ``None`` when the request was not rerouted."""
    note = holds.reroute
    if note is None:
        return None
    return incident_reroute(
        request_id=holds.request_id,
        org_id=holds.org_id,
        requested=note.requested,
        routed=note.routed,
        recorded_at_ns=recorded_at_ns,
        decision_source=note.decision_source,
    )


def with_routing(holds: RequestHolds, record: AuditRecord) -> AuditRecord:
    """Copy the reroute note into ``record.detail["routing"]`` (a no-op when not rerouted)."""
    note = holds.reroute
    if note is None:
        return record
    routing = MappingProxyType(
        {
            "decision_source": note.decision_source,
            "isolated_model": note.isolated_model,
            "fallback": note.routed,
            "reason": note.reason,
        }
    )
    return AuditRecord(
        request_id=record.request_id,
        org_id=record.org_id,
        phase=record.phase,
        outcome=record.outcome,
        recorded_at_ns=record.recorded_at_ns,
        detail={**record.detail, ROUTING_KEY: dict(routing)},
        schema_version=record.schema_version,
    )
