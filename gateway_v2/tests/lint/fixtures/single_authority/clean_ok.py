"""Not a violation: a Transformation whose kind is dynamic (copied from an existing edit)."""
from gateway_v2.domain import Transformation


def copy_edit(item: Transformation) -> Transformation:
    return Transformation(item.kind, item.span, "x")
