"""admit(): identity -> kill-switch -> quota. Returns a Grant or an ErrorSpec."""

from __future__ import annotations

import time
from dataclasses import dataclass

from rvproto.admit import posture
from rvproto.admit.identity import Identity
from rvproto.admit.killswitch import KillSwitch
from rvproto.admit.quota import Gcra, TokenLease
from rvproto.domain.request import ErrorSpec, Principal
from rvproto.runtime.store import StoreError


@dataclass(frozen=True, slots=True)
class Grant:
    principal: Principal
    cost_tokens: int
    round_trips: int


class Admission:
    def __init__(self, identity: Identity, ks: KillSwitch, gcra: Gcra, lease: TokenLease) -> None:
        self.identity = identity
        self.ks = ks
        self.gcra = gcra
        self.lease = lease

    async def admit(self, api_key: str | None, cost_tokens: int, model: str) -> Grant | ErrorSpec:
        if not api_key:
            return posture.MISSING_KEY
        trips = 0
        key_hash, principal = self.identity.cached(api_key)
        state = self.ks.state()
        if state == "stale":
            return posture.KILLSWITCH_STALE
        if state == "engaged":
            return posture.KILLSWITCH_ON
        if self.ks.model_killed(model):
            return posture.KILLSWITCH_MODEL
        if principal is None:
            try:
                trips += 1
                principal = await self.identity.fetch(key_hash)
            except StoreError:
                return posture.STORE_UNAVAILABLE
            if principal is None:
                return posture.INVALID_KEY
        if self.ks.org_killed(principal.org_id):
            return posture.KILLSWITCH_ORG
        wait = self.gcra.check(principal.org_id, principal.rate_per_s, principal.burst, time.monotonic())
        if wait is not None:
            return posture.rate_limited(wait)
        if not self.lease.try_local(principal.org_id, cost_tokens):
            try:
                trips += 1
                ok = await self.lease.refill(principal.org_id, cost_tokens)
            except StoreError:
                return posture.STORE_UNAVAILABLE
            if not ok:
                return posture.quota_exhausted()
        return Grant(principal, cost_tokens, trips)
