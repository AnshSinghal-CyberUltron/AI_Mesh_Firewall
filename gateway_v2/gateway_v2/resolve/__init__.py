"""Single decision authority. Resolver is pure.

GW07 (§10.5.3, C22): the ONE pure security-decision function and the frozen
adapter that drops it into the shipped ``OutputResolver`` seam by shape. Every
module in this package imports ONLY ``gateway_v2.domain`` (the "Resolve imports
only domain" import-linter contract).
"""

from gateway_v2.resolve.decision import PlanResolver
from gateway_v2.resolve.resolver import resolve

__all__ = (
    "PlanResolver",
    "resolve",
)
