"""Typed fail-closed exceptions for the output path (GW33 D-31).

Both subclass `ValueError` so every pre-existing `except ValueError` still sees them, but the
pipelines catch them with EXPLICIT arms placed before the generic `except Exception` (which sets
`incomplete`, records and re-raises). Imports nothing: `edge` raises `OutputLengthExceeded` from
above, `egress` raises `RewriteVerificationFailure` from `rewrite_verify`.
"""
from __future__ import annotations

__all__ = ("OutputLengthExceeded", "RewriteVerificationFailure")


class RewriteVerificationFailure(ValueError):
    """A rewrite could not be byte-verified: terminal code `rewrite_unverified`."""


class OutputLengthExceeded(ValueError):
    """The provider exceeded `output_ceiling_chars()`: terminal code `output_length_exceeded`."""
