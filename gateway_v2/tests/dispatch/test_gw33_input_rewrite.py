"""GW33 task 4: request-side REWRITE is verified like a redaction; the `rewrite` bit is untouched."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from gateway_v2.dispatch.transform import GatewayRequest
from gateway_v2.dispatch.verify import VerificationFailure, apply_and_verify
from gateway_v2.domain import (
    Category,
    Decision,
    Disposition,
    Finding,
    FindingDisposition,
    FindingStatus,
    Span,
    Transformation,
)
from gateway_v2.resolve import authorize

_PKG = Path(__file__).resolve().parents[2] / "gateway_v2"


def _decision(spans: tuple[Span, ...], edits: tuple[Transformation, ...]) -> Decision:
    finding = Finding("ip", "v", Category.IP_LEAK, FindingStatus.EXECUTED, 1.0, spans, None)
    return Decision(
        Disposition.REWRITE, (FindingDisposition("ip", Disposition.REWRITE, 0),), edits,
        (finding,), "1.1.x", ("r",), (),
    )


def _request(*deltas: str) -> GatewayRequest:
    return GatewayRequest(
        destination="d", model="m", text_deltas=tuple(("content", d) for d in deltas),
    )


def test_a_rewrite_span_is_replaced_across_deltas_and_verified() -> None:
    request = _request("host 10.0.", "0.1 ok")
    span = Span(5, 13)
    decision = _decision((span,), (Transformation("rewrite", span, "[ip]"),))
    verified = apply_and_verify(decision, request)
    assert "".join(text for _c, text in verified.request.text_deltas) == "host [ip] ok"
    assert verified.request.text_deltas[0][1] == "host [ip]"  # replacement stays in the start delta


def test_an_uncovered_rewrite_span_raises_verification_failure() -> None:
    request = _request("see 10.0.0.1 now")
    decision = _decision((Span(4, 12),), (Transformation("rewrite", Span(4, 8), "[ip]"),))
    with pytest.raises(VerificationFailure):
        apply_and_verify(decision, request)
    with pytest.raises(VerificationFailure):
        apply_and_verify(_decision((Span(4, 12),), ()), request)


def test_a_surviving_original_raises_verification_failure() -> None:
    request = _request("see 10.0.0.1 now")
    span = Span(4, 12)
    # The "replacement" re-embeds the original: the survivor check refuses it.
    decision = _decision((span,), (Transformation("rewrite", span, "x 10.0.0.1 x"),))
    with pytest.raises(VerificationFailure):
        apply_and_verify(decision, request)


def test_authorize_mints_for_a_rewrite_decision_and_stamps_rewrite() -> None:
    decision = _decision((Span(0, 1),), (Transformation("rewrite", Span(0, 1), "[ip]"),))
    minted = authorize(decision)
    assert minted is not None
    assert str(Disposition.REWRITE) == "rewrite"


def test_no_edge_caller_passes_rewrite_true() -> None:
    """The GW11 `rewrite` bit (local seam, zero provider calls) is never set by GW33."""
    offenders: list[str] = []
    for path in sorted((_PKG / "edge").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "rewrite" and not (
                        isinstance(kw.value, ast.Constant) and kw.value.value is False
                    ):
                        offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []
