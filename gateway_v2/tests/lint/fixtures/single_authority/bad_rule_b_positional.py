"""Planted violation: a redact edit built outside the resolver (positional kind)."""
from gateway_v2.domain import Span, Transformation


def second_editor() -> Transformation:
    return Transformation("redact", Span(0, 1), "x")
