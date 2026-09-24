"""Capacity failures — refuse to start rather than start degraded."""

from __future__ import annotations


class CapacityUnavailable(RuntimeError):
    """Detected capacity is below the minimum required to serve one request."""


class CapacityUnset(RuntimeError):
    """A bound that depends on an unknown signal is explicitly unset, not guessed."""
