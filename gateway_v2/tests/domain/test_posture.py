"""Posture vocabulary: the OVERLOAD_SHED code and the reused Retry-After floor (R2-07/R2-08)."""

from __future__ import annotations

from gateway_v2.domain import posture


def test_overload_shed_code_value() -> None:
    """R5.1/R6.1: the shed posture code is the frozen string `edge` maps to a 503."""
    assert posture.OVERLOAD_SHED == "overload_shed"


def test_min_retry_after_floor_reused_not_redefined() -> None:
    """R6.1: the Retry-After floor already lives in `domain.posture`; `admit` reuses this value."""
    assert posture.MIN_RETRY_AFTER_S == 1.0


def test_overload_shed_reexported_from_domain_package() -> None:
    """The code joins the one shared vocabulary, re-exported like the other posture codes."""
    from gateway_v2.domain import OVERLOAD_SHED

    assert OVERLOAD_SHED is posture.OVERLOAD_SHED
