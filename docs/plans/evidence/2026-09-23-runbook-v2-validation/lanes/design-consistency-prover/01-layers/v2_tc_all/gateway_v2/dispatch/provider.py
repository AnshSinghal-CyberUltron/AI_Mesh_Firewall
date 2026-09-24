"""ProviderClient protocol — rb.md L2159, L2184, L2603, L2754."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from gateway_v2.resolve.decision import DispatchAuthorization

# L2184 'the provider client's entry point requires a DispatchAuthorization value that
#        resolve/ only mints for non-blocking dispositions'
# L2754 'One ProviderClient entry point, requiring a DispatchAuthorization that only resolve/ mints'


class ProviderStream(Protocol):
    def __aiter__(self) -> AsyncIterator[bytes]: ...


class ProviderClient(Protocol):
    async def open_stream(self, auth: DispatchAuthorization, payload: bytes) -> ProviderStream: ...


def require_authorization(auth: object) -> DispatchAuthorization:
    if not isinstance(auth, DispatchAuthorization):
        raise TypeError("provider entry point requires a DispatchAuthorization")
    return auth
