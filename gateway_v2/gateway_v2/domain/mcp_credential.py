"""GW17b value types for an upstream MCP credential.

A credential is a secret held in memory only for the duration of one call. These types make it
hard to leak by accident: the secret fields are `repr=False`, `repr`/`str` print a constant,
and pickling or copying raises. Nothing here logs, audits, scans or serialises a secret.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NoReturn

KINDS = frozenset({"bearer", "basic", "header", "oauth"})
"""Credential kinds the gateway can attach. `query_param` is deliberately absent (DD-16)."""
_REDACTED = "<redacted>"


@dataclass(frozen=True, slots=True)
class UpstreamCredential:
    """One server's upstream credential, as fetched from control."""

    kind: str
    token: str = field(repr=False)
    username: str = field(default="", repr=False)
    header_name: str = ""
    expires_at: float | None = None
    refreshable: bool = False
    version: int = 0

    def __repr__(self) -> str:
        return _REDACTED

    def __str__(self) -> str:
        return _REDACTED

    def __reduce__(self) -> NoReturn:
        raise TypeError("credentials are not serialisable")

    def __copy__(self) -> NoReturn:
        raise TypeError("credentials are not copyable")

    def __deepcopy__(self, memo: object) -> NoReturn:
        raise TypeError("credentials are not copyable")


@dataclass(frozen=True, slots=True)
class RenderedHeaders:
    """Final `(name, value)` pairs for `upstream.headers`. Same redaction behaviour."""

    pairs: tuple[tuple[str, str], ...] = field(repr=False)

    def __repr__(self) -> str:
        return _REDACTED

    def __str__(self) -> str:
        return _REDACTED

    def __reduce__(self) -> NoReturn:
        raise TypeError("rendered headers are not serialisable")

    def __copy__(self) -> NoReturn:
        raise TypeError("rendered headers are not copyable")

    def __deepcopy__(self, memo: object) -> NoReturn:
        raise TypeError("rendered headers are not copyable")


@dataclass(frozen=True, slots=True)
class CredentialRefusal:
    """A refusal carrying a declared value-code only. Never a message built from a secret."""

    code: str
