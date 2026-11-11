"""GW29 explicit dev/test pass-through incident gate (design 6.1: "named, test/dev only").

The default of every incident seam is FAIL CLOSED (an unwired route refuses with cause ``unwired``).
A dev profile or a test that wants requests to flow wires this gate by name. It never refuses, never
reroutes, and sends the requested model name upstream unchanged, so it is NEVER a production
default: the production profile binds the real ``IncidentGate`` and a boot test asserts it is
not this class.
"""

from __future__ import annotations

from gateway_v2.domain.incident import NO_ROUTED_MODEL, GateVerdict

__all__ = ("PassThroughIncidentGate",)


class PassThroughIncidentGate:
    """Proceeds on every call with the requested model verbatim (dev/test only)."""

    __slots__ = ()

    def decide(self, *, org_id: str, requested_model: str, request_id: str) -> GateVerdict:
        del org_id, request_id
        return GateVerdict.proceed(requested_model or NO_ROUTED_MODEL)

    def recheck(self, *, org_id: str, model: str) -> GateVerdict:
        del org_id
        return GateVerdict.proceed(model or NO_ROUTED_MODEL)

    def decide_org(self, *, org_id: str, request_id: str) -> GateVerdict:
        del org_id, request_id
        return GateVerdict.proceed(NO_ROUTED_MODEL)
