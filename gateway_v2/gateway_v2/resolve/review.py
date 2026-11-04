"""The review flag (GW33 C2, D-10). Pure; the resolver is its only producer."""
from __future__ import annotations

from collections.abc import Sequence

from gateway_v2.domain import Disposition, FindingDisposition, Phase

__all__ = ("review_required",)


def review_required(per_finding: Sequence[FindingDisposition], phase: Phase) -> bool:
    """True iff an OUTPUT-phase rule produced a non-ALLOW disposition for some finding."""
    return phase is Phase.OUTPUT and any(
        item.disposition is not Disposition.ALLOW for item in per_finding
    )
