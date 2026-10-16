"""The single legal mint site for ``DispatchAuthorization`` (C2, R1.5/R4.2).

``authorize(decision)`` is the ONE call that mints a ``DispatchAuthorization``.
A BLOCK ``Decision`` is terminal: no authorization is minted, so no object
exists for any caller to pass to the provider entry (LGW11-2). ALLOW / FLAG /
REDACT (including REWRITE, which the resolver maps to ``Disposition.REDACT``)
mint one stamped with the decision identity.

Imports ONLY ``gateway_v2.domain`` (the "Resolve imports only domain"
import-linter contract). The full-module-path import of the module-private
``_mint`` is the only way to reach the mint seam; this is legal because
``domain`` is a lower layer than ``resolve``. The provenance AST gate restricts
``_mint`` (and every other forgery form) to the ``resolve`` package — this file
is that single allowed call site. Pure: no clock, no I/O, no mutation.
"""

from __future__ import annotations

from gateway_v2.domain import Decision, Disposition
from gateway_v2.domain.dispatch_authorization import DispatchAuthorization, _mint


def authorize(decision: Decision) -> DispatchAuthorization | None:
    """Mint an authorization for a non-terminal ``Decision``; ``None`` for BLOCK.

    Returns ``None`` iff ``decision.disposition is Disposition.BLOCK`` (terminal,
    so no authorization object exists). Otherwise mints one for ALLOW / FLAG /
    REDACT, carrying the decision identity (``plan_version``, ``disposition``).
    """
    if decision.disposition is Disposition.BLOCK:
        return None
    return _mint(plan_version=decision.plan_version, disposition=str(decision.disposition))


__all__ = ("authorize",)
