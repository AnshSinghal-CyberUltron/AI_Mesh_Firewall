"""The provider scripts the matrix cells select with ``marker(name)``."""

from __future__ import annotations

from collections.abc import Sequence

from gateway_v2.dispatch.provider import UpstreamEvent
from tests.openai_conformance.sdk_matrix.harness import HOLD, ScriptedProvider

__all__ = ["SCRIPTS", "scripted_provider"]


def _ev(*pairs: tuple[str, str], final: bool = False) -> UpstreamEvent:
    return UpstreamEvent(text_deltas=tuple(pairs), final=final)


_CALL = (
    _ev(("output_tool_id#0", "call_abc"), ("output_tool_name#0", "get_weather")),
    _ev(("output_tool_arguments#0", '{"city": "Pa')),
    _ev(("output_tool_arguments#0", 'ris", "unit": "c"}')),
    _ev(("finish_reason", "tool_calls"), final=True),
)

SCRIPTS: dict[str, Sequence[UpstreamEvent]] = {
    "plain": (_ev(("content", "Hello ")), _ev(("content", "from the "), ("content", "gateway."),
                                              final=True)),
    "tool": _CALL,
    "two_tools": (
        _ev(("output_tool_id#0", "call_a"), ("output_tool_name#0", "get_weather")),
        _ev(("output_tool_arguments#0", '{"city":"Paris"}')),
        _ev(("output_tool_id#1", "call_b"), ("output_tool_name#1", "get_weather")),
        _ev(("output_tool_arguments#1", '{"city":"Rome"}')),
        _ev(("finish_reason", "tool_calls"), final=True),
    ),
    "slow": (_ev(("content", "first ")), _ev(("content", "second ")), HOLD),
    "blocked_output": (_ev(("content", "the key is AKIAIOSFODNN7EXAMPLE ok"), final=True),),
    "upstream_timeout": (
        UpstreamEvent(text_deltas=(), final=True, gateway_error="upstream_timeout"),
    ),
    "embeddings": (_ev(("embeddings_input", "[0.25, -0.5, 0.125]"), final=True),),
    "json": (_ev(("content", '{"answer": "forty-two"}'), final=True),),
}


def scripted_provider() -> ScriptedProvider:
    return ScriptedProvider(SCRIPTS, default="plain")
