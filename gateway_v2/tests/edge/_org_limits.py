"""GW27 test helper: a real ``OrgLimits`` for routes built directly in tests."""
from __future__ import annotations

from gateway_v2.admit.org_limits import OrgLimits
from gateway_v2.runtime.resources import ResourceContract


def org_limits_for(contract: ResourceContract) -> OrgLimits:
    return OrgLimits(contract)


class NoopOrgLimits:
    """Admits everything; a stand-in for fakes that never exercise the org gate."""

    def check(self, org: str, controls: object, cost: int, *, request_id: str) -> None:
        return None

    def refund_tokens(self, org: str, cost: int) -> None:
        return None
