"""GW33 task 9.2a / D-13: a provider that ignores the clamp is cut with the declared code."""
from __future__ import annotations

import asyncio

import pytest

from gateway_v2.domain import Action, Category, StreamingMode, posture
from gateway_v2.edge import errors
from gateway_v2.edge.output_length import bounded_upstream
from gateway_v2.egress.output_errors import OutputLengthExceeded
from gateway_v2.egress.stream import UpstreamChunk
from tests.e2e.gw33_support import contract, plan_of, rule, run


async def _source(sizes: list[int]):  # type: ignore[no-untyped-def]
    for index, size in enumerate(sizes):
        yield UpstreamChunk(text_deltas=(("output_content", "x" * size),), final=index == len(sizes) - 1)


def _drain(source) -> list[UpstreamChunk]:  # type: ignore[no-untyped-def]
    async def go() -> list[UpstreamChunk]:
        return [chunk async for chunk in source]
    return asyncio.run(go())


def test_a_compliant_provider_is_untouched_and_an_oversize_one_is_cut() -> None:
    ceiling = contract().output_ceiling_chars()
    assert len(_drain(bounded_upstream(_source([10, 20]), ceiling))) == 2
    with pytest.raises(OutputLengthExceeded):
        _drain(bounded_upstream(_source([ceiling, 1]), ceiling))
    with pytest.raises(OutputLengthExceeded):  # cumulative across chunks and channels
        _drain(bounded_upstream(_source([ceiling // 2 + 1, ceiling // 2 + 1]), ceiling))


@pytest.mark.parametrize("mode", list(StreamingMode))
def test_the_pipeline_ends_with_the_declared_code_never_a_silent_truncation(
    mode: StreamingMode,
) -> None:
    for action in (Action.REDACT, Action.FLAG):  # enforcing and pass-through
        plan = plan_of(mode, rule("r", Category.PII, action))
        result = run(plan, [("output_content", "abc")] * 3, wrap=lambda s: bounded_upstream(s, 4))
        assert result.codes == [posture.OUTPUT_LENGTH_EXCEEDED], action
        assert result.stats.incomplete is True  # type: ignore[attr-defined]
        assert result.stats.error == posture.OUTPUT_LENGTH_EXCEEDED  # type: ignore[attr-defined]


def test_the_four_gw33_codes_render_through_the_envelope_with_a_generic_body() -> None:
    for code in (posture.REWRITE_UNVERIFIED, posture.FINAL_CHECK_UNAPPLIED,
                 posture.OUTPUT_LENGTH_EXCEEDED, posture.CANARY_UNAVAILABLE):
        response = errors.render_http(code, request_id="zs-1")
        assert response.status == 503
        body = response.json()
        assert body["error"]["type"] == "service_unavailable"  # type: ignore[index]
        assert body["error"]["code"] == code  # type: ignore[index]
