"""GW35 bound on audit records written for a request that names no tenant.

A failed-credential request has no org, so its record goes to a reserved stream. An unauthenticated
caller controls how often that happens, so the stream's write rate is bounded per window. The
derivation lives here, split out of `runtime/resources.py` (800 lines, the size gate) the way
`resources_vector.py` is.

The multiplier is an unmeasured starting value. It is an owner decision pending the signed capacity.
"""

from __future__ import annotations

from gateway_v2.runtime.errors import CapacityUnavailable
from gateway_v2.runtime.resources import ResourceContract

_UNAUTH_AUDIT_RATE_MULT = 2  # records per metrics window = workers x mult (unmeasured; owner decision)


def unauth_audit_rate(contract: ResourceContract) -> int:
    """Unauthenticated audit records allowed per ``contract.metrics_window_s()``.

    Raises ``CapacityUnavailable`` when the derived bound is below one, so the caller drops
    (fails closed) rather than writing without a bound.
    """
    rate = contract.workers() * _UNAUTH_AUDIT_RATE_MULT
    if rate < 1:
        raise CapacityUnavailable("unauthenticated audit rate below one")
    return rate
