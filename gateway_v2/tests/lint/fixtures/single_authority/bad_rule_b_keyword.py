"""Planted violation: a rewrite edit built outside the resolver (keyword kind)."""
from gateway_v2.domain import Span, Transformation


def second_editor() -> Transformation:
    return Transformation(kind="rewrite", span=Span(0, 1), replacement="x")
