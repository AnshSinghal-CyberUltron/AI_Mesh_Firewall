"""Sealed Dispatch_Authorization value type and its module-private mint seam (C1, R1).

A `DispatchAuthorization` is proof that a request is authorized to reach a provider.
It carries the decision identity it authorizes (`plan_version`, `disposition`) and a
module-private `_Witness` reference. `__post_init__` validates the witness by IDENTITY
(`is`), so a naive `DispatchAuthorization(...)` from outside this module — which cannot
name the private `_WITNESS` singleton — raises at construction (R1.2, the sealed
constructor).

Honest residual (C11): the sealed constructor closes naive construction only. It does
NOT by itself close `object.__new__` + `object.__setattr__` forgery, nor
`dataclasses.replace` on a valid auth. Those are closed STATICALLY by the provenance
AST gate (`lint/check_dispatch_provenance.py`) and DYNAMICALLY by the runtime assertion
at the single provider entry point. This matches C11's own position: "not enforceable by
a sealed constructor alone."

This module lives in the lowest layer (`domain`) and imports nothing from gateway_v2
above it, so both `resolve/` (to mint, via `_mint`) and `dispatch/` (to require) import
`DispatchAuthorization` from the same layer. Minting is restricted to `resolve/` by the
provenance gate, not by import visibility (R1.4).
"""

from __future__ import annotations

from dataclasses import dataclass


class _Witness:
    """Module-private provenance token. The ONLY instance is `_WITNESS` below.

    It is not exported and cannot be named from outside this module without reaching
    into module internals (which the provenance gate flags). `__slots__ = ()` makes the
    singleton an immutable, zero-attribute instance.
    """

    __slots__ = ()


_WITNESS = _Witness()
"""The single provenance witness. Only `_mint` stamps it; `__post_init__` checks it by `is`."""


@dataclass(frozen=True, slots=True)
class DispatchAuthorization:
    """Proof a request is authorized to reach a provider (R1.1).

    Frozen + slotted. Carries the decision identity it authorizes (`plan_version`,
    `disposition`) and a private witness reference. `__post_init__` validates the witness
    by IDENTITY (`is`), so a naive `DispatchAuthorization(...)` from outside — which cannot
    name `_WITNESS` — raises at construction (R1.2, the sealed constructor). This closes the
    naive-construction hole; it does NOT by itself close `object.__new__`/setattr forgery
    (that is the gate + runtime assertion).
    """

    plan_version: str
    disposition: str
    _witness: _Witness

    def __post_init__(self) -> None:
        if self._witness is not _WITNESS:
            raise ValueError("DispatchAuthorization is not constructible outside resolve/ mint")


def _mint(plan_version: str, disposition: str) -> DispatchAuthorization:
    """Module-private mint. Stamps the one `_WITNESS` so `__post_init__` passes.

    `resolve/` reaches this through `authorize()`; the provenance gate flags any call to
    `_mint` (or direct construction / replace / `__new__`) outside `resolve/`.
    """
    return DispatchAuthorization(
        plan_version=plan_version,
        disposition=disposition,
        _witness=_WITNESS,
    )


__all__ = ("DispatchAuthorization",)
