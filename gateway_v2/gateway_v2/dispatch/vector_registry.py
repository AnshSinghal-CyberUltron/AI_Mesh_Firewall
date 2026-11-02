"""GW30 exact-match vector-store registry — no silent fallback (R7).

This module owns :class:`VectorRegistry`, the selector that resolves a requested
``store_id`` to **exactly one** registered
:class:`~gateway_v2.dispatch.vector_adapter.Vector_Adapter`. It is the direct
analogue of ``dispatch/routing.py``'s ``DispatchRouter``: a pure,
deterministic, slotted (``__slots__``) selector with no clock, no randomness, no I/O
and no module-level mutable state. The one difference from ``DispatchRouter`` is the
selection key — the registry selects by an **explicitly requested identifier**, never
derives "any available" — but the purity and exact-match discipline are identical.

What this module defines (task 8.1 scope — the registry ONLY):

* :class:`VectorRegistry` — constructed with an **injected** ``store_id -> Vector_Adapter``
  mapping (built in ``edge`` / ``dispatch``; the registry holds no module-level mutable
  table). ``select(store_id)`` returns the connector registered for that **exact** id, or
  the frozen ``store_not_configured`` outcome when no connector matches — routing to **no**
  connector and leaving tenant data / query state untouched (R7.1, R7.2).

**No silent fallback (R7).** ``select`` never chooses by "any available", load,
round-robin, or any criterion other than the exact id (R7.4) — the v1
``_resolve_vector_client`` anti-pattern is explicitly barred. An unregistered id yields the
declared ``store_not_configured`` code on a frozen :class:`AdapterResult` (fail-closed, a
declared ``Vector_Posture_Code`` on a frozen result rather than ``None`` or a raised
exception), so the caller distinguishes a miss **by ``code`` alone**. Because selection is a
single exact-match lookup, for any one operation the count of distinct connectors the
registry can hand back is **at most one** (R7.3, R7.5); a connector-produced
``provider_unreachable`` / ``unsupported_operation`` is the caller's concern and is never a
cue for the registry to try another connector — the registry has already returned.

**Layering.** The registry lives in ``dispatch`` alongside the ``Vector_Adapter`` protocol
and value types it reads, and imports only those sibling value types plus the ``domain``
posture codes — the same single downward ``domain`` edge the rest of ``dispatch`` uses. It
adds no new layer edge, holds no capacity literal, and holds no module-level mutable table:
the connector mapping is injected at construction, never declared at module scope.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from gateway_v2.dispatch.vector_adapter import AdapterResult, Vector_Adapter
from gateway_v2.domain import posture

__all__: tuple[str, ...] = ("VectorRegistry",)


class VectorRegistry:
    """Exact-match ``store_id -> Vector_Adapter`` selector, no silent fallback (R7).

    Mirrors ``DispatchRouter``'s purity: there is no clock, no ``random``, no I/O, and the
    only state is the injected, read-only connector mapping captured at construction. The
    same inputs always produce the same outcome, so the no-silent-fallback behaviour is a
    property of ``select`` itself rather than of any mutable configuration carried on the
    registry (task 8.2's property test).

    The injected mapping is defensively wrapped in a ``MappingProxyType`` so the registry
    cannot mutate — nor be mutated through — its connector table after construction, keeping
    the registry stateless in the only sense that matters for selection: the set of
    registered connectors is fixed for the registry's lifetime and selection reads it without
    side effects.
    """

    __slots__ = ("_connectors",)

    def __init__(self, connectors: Mapping[str, Vector_Adapter]) -> None:
        """Capture the injected ``store_id -> Vector_Adapter`` mapping (R7.1).

        The mapping is built in ``edge`` / ``dispatch`` (e.g. from the 13-connector factory)
        and injected here; the registry never constructs or mutates it and never consults a
        module-level table. A read-only view is stored so the registered set is immutable for
        the registry's lifetime.
        """
        self._connectors: Mapping[str, Vector_Adapter] = MappingProxyType(dict(connectors))

    def select(self, store_id: str) -> Vector_Adapter | AdapterResult:
        """Resolve ``store_id`` to its one registered connector, or ``store_not_configured``.

        Pure and deterministic. Returns the :class:`Vector_Adapter` registered for that
        **exact** ``store_id`` (R7.1), or an :class:`AdapterResult` carrying
        ``posture.STORE_NOT_CONFIGURED`` when no connector matches (R7.2) — routing to **no**
        connector and leaving tenant data / query state untouched. Selection is a single
        exact-key lookup: it never falls back to "any available", load, round-robin, or any
        other criterion (R7.4), and it can hand back at most one connector for the id
        (R7.3, R7.5). The miss is a frozen declared-code result, never ``None`` and never a
        raised exception, so the caller tells a miss from a hit by ``code`` alone.
        """
        connector = self._connectors.get(store_id)
        if connector is None:
            return AdapterResult(code=posture.STORE_NOT_CONFIGURED)
        return connector
