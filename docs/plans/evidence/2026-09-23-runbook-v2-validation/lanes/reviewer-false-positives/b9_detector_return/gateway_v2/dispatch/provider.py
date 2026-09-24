"""Provider entry point requires a DispatchAuthorization (type from domain/)."""

from __future__ import annotations

from gateway_v2.contracts.domain.decision import DispatchAuthorization
from gateway_v2.contracts.domain.ports import ProviderClient, ProviderStream
from gateway_v2.contracts.domain.taxonomy import Disposition


def open_authorized(
    client: ProviderClient, auth: DispatchAuthorization, payload: bytes
) -> ProviderStream:
    if (
        not isinstance(auth, DispatchAuthorization)
        or auth.decision.disposition is Disposition.BLOCK
    ):
        raise TypeError("provider entry point requires a non-BLOCK DispatchAuthorization")
    return client.open_stream(auth, payload)
