"""The single per-request id mint for tenant-scoped requests (finding F2, request-id-uniqueness).

``mint_tenant_request_id(org_id)`` returns ``zs-{org_id}-{24 hex}`` from a random source. It takes
no request content, so an id can never be derived from (or reveal equality of) content. It is
called once per request, and the id is carried on ``RequestHolds``.

Fail closed (req 2.8): any entropy failure, or a result that is not exactly ``2 * _ID_BYTES``
lowercase hex characters, raises :class:`RequestIdUnavailable`. It never returns a partial, empty
or hashed id.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable

# A formatting width (12 bytes -> 24 hex, the shipped width), not a capacity bound.
_ID_BYTES = 12
_HEX = frozenset("0123456789abcdef")


class RequestIdUnavailable(Exception):
    """No request id could be minted; the request must be refused (no id exists)."""


def mint_tenant_request_id(
    org_id: str, *, token_hex: Callable[[int], str] = secrets.token_hex
) -> str:
    """Return ``zs-{org_id}-{hex}``; raise :class:`RequestIdUnavailable` on any failure."""
    try:
        suffix = token_hex(_ID_BYTES)
    except Exception:
        raise RequestIdUnavailable from None
    if not isinstance(suffix, str) or len(suffix) != 2 * _ID_BYTES or not _HEX.issuperset(suffix):
        raise RequestIdUnavailable
    return f"zs-{org_id}-{suffix}"
