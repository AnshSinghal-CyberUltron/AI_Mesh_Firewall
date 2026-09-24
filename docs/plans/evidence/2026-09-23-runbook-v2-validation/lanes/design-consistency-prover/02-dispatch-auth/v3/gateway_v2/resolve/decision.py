"""Decision types (domain/) + the ONLY minting function for DispatchAuthorization."""

from __future__ import annotations

from gateway_v2.domain.decision import Decision, DispatchAuthorization, Transformation
from gateway_v2.domain.taxonomy import Disposition, Phase

__all__ = (
    "Decision",
    "DispatchAuthorization",
    "Disposition",
    "Phase",
    "Transformation",
    "mint_dispatch_authorization",
)


def mint_dispatch_authorization(decision: Decision) -> DispatchAuthorization | None:
    if decision.disposition is Disposition.BLOCK:
        return None
    return DispatchAuthorization(decision)
