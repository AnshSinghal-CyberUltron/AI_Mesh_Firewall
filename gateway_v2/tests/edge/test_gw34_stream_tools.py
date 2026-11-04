"""GW34 Task 9: streamed tool calls through the route; assembler termination in every pipeline."""
from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import pytest

from gateway_v2.edge import chat_stream
from gateway_v2.edge.chat_stream import ChatStream
from gateway_v2.edge.wire.tool_stream import ToolCallAssembler
from gateway_v2.egress.output_guard import MinimalOutputResolver
from gateway_v2.egress.stream import DownstreamFrame, StreamStats
from gateway_v2.egress.strict import StrictWithholdPipeline
from gateway_v2.runtime.strict_metrics import StrictMetrics
from tests.edge.gw34_stream_harness import Reply, Scripted, call, content, deltas
from tests.edge.test_gw34_upstream_body import _pydantic_tool
from tests.egress.test_lgw12b_stream import _for_plan, _plan, _rule

_USER = {"role": "user", "content": "hi"}
_TOOLS = [_pydantic_tool()]


def _tool_events(name: str = "get_weather", args: str = '{"city":"Paris"}') -> list[Any]:
    return [
        deltas(("output_tool_id#0", "call_1"), ("output_tool_name#0", name)),
        deltas(("output_tool_arguments#0", args)),
        deltas(("finish_reason", "tool_calls"), final=True),
    ]


def _stream(events: list[Any], **extra: Any) -> Reply:
    return call(Scripted(events), {"messages": [_USER], "stream": True, "tools": _TOOLS, **extra})


def _tool_bytes(reply: Reply) -> bool:
    return b"tool_calls" in reply.body or b"call_1" in reply.body or b"Paris" in reply.body


def test_an_enforcing_stream_releases_one_complete_call_after_the_content() -> None:
    reply = _stream(_tool_events())
    assert reply.status == 200 and reply.is_sse
    calls = [d["tool_calls"] for d in reply.deltas() if "tool_calls" in d]
    assert calls == [[{"index": 0, "id": "call_1", "type": "function",
                       "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'}}]]
    assert reply.frames()[-1]["choices"][0]["finish_reason"] == "tool_calls"
    assert reply.deltas()[0]["role"] == "assistant"
    assert reply.body.count(b"data: [DONE]") == 1 and reply.body.rstrip().endswith(b"[DONE]")


def test_a_secret_in_streamed_arguments_is_redacted_and_the_call_stays_valid_json() -> None:
    args = json.dumps({"ssn": "123-45-6789"})
    reply = _stream(_tool_events(args=args))
    assert reply.status == 200
    [fragment] = [d["tool_calls"][0] for d in reply.deltas() if "tool_calls" in d]
    assert json.loads(fragment["function"]["arguments"]) == {"ssn": "[REDACTED]"}
    assert b"123-45-6789" not in reply.body


def test_json_mode_content_is_released_once_after_validation() -> None:
    reply = call(Scripted([content('{"a":', final=False), content("1}", final=True)]),
                 {"messages": [_USER], "stream": True, "response_format": {"type": "json_object"}})
    assert reply.status == 200
    text = "".join(d.get("content", "") for d in reply.deltas())
    assert json.loads(text) == {"a": 1}
    bad = call(Scripted([content("not json", final=True)]),
               {"messages": [_USER], "stream": True, "response_format": {"type": "json_object"}})
    assert bad.status != 200 and json.loads(bad.body)["error"]["code"] == "output_blocked"
    assert b"not json" not in bad.body


# ----------------------------------------------------------- pipelines under test


def _pipelines() -> dict[str, tuple[Any, bool]]:
    """name -> (pipeline factory, enforcing?) for all three pipelines."""
    return {
        "passthrough": (lambda: _for_plan(_plan()), False),
        "incremental": (lambda: _for_plan(_plan(_rule("r1"))), True),
        "strict": (
            lambda: StrictWithholdPipeline(
                resolver=MinimalOutputResolver(), enforcing_output=True,
                max_buffer_bytes=1 << 30, metrics=StrictMetrics(), clock=time.perf_counter_ns,
            ),
            True,
        ),
    }


@pytest.mark.parametrize("kind", ["passthrough", "incremental", "strict"])
def test_an_assembler_termination_releases_nothing_in_every_pipeline(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory, enforcing = _pipelines()[kind]
    monkeypatch.setattr(ChatStream, "_build_pipeline", lambda self, plan: factory())
    monkeypatch.setattr(chat_stream, "enforcing_output_rules", lambda plan: enforcing)
    reply = _stream(_tool_events(name="rm_rf"))  # undeclared name
    assert reply.status == 403 and not reply.is_sse, (kind, reply.status)
    assert json.loads(reply.body)["error"]["code"] == "output_blocked"
    assert not _tool_bytes(reply)


class _TerminalPipeline:
    """Forwards every frame, then ends the way an egress terminate does: a terminal frame and a
    RETURNED ``StreamStats(incomplete=True)`` (it does not raise)."""

    def __init__(self, code: str) -> None:
        self.code = code

    async def run(self, chunks: AsyncIterator[Any], send: Any, killed: Any) -> StreamStats:
        async for chunk in chunks:
            await send(DownstreamFrame(text_deltas=chunk.text_deltas))
        await send(DownstreamFrame(text_deltas=(), error_code=self.code))
        stats = StreamStats()
        stats.incomplete, stats.error, stats.output_action = True, self.code, "block"
        return stats


@pytest.mark.parametrize("code", ["output_blocked", "scan_failure", "holdback_overflow", "killed"])
def test_an_egress_terminate_with_a_withheld_call_drops_it_and_never_finishes(
    code: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    finished: list[int] = []
    real_finish = ToolCallAssembler.finish

    async def spy(self: ToolCallAssembler) -> Any:
        finished.append(1)
        return await real_finish(self)

    monkeypatch.setattr(ToolCallAssembler, "finish", spy)
    monkeypatch.setattr(ChatStream, "_build_pipeline", lambda self, plan: _TerminalPipeline(code))
    monkeypatch.setattr(chat_stream, "enforcing_output_rules", lambda plan: True)
    reply = _stream(_tool_events())
    assert reply.status != 200 and not reply.is_sse, (code, reply.status)
    assert not _tool_bytes(reply) and b"finish_reason" not in reply.body
    assert finished == [], "finish() must not be called after an egress terminate"


def test_a_terminate_after_the_first_byte_is_an_error_frame_with_no_tool_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ChatStream, "_build_pipeline",
                        lambda self, plan: _TerminalPipeline("output_blocked"))
    monkeypatch.setattr(chat_stream, "enforcing_output_rules", lambda plan: True)
    reply = _stream([content("some text ", final=False), *_tool_events()])
    assert reply.status == 200 and reply.is_sse
    assert b"output_blocked" in reply.body and not _tool_bytes(reply)
    assert b'"finish_reason":"tool_calls"' not in reply.body
    assert reply.body.count(b"[DONE]") == 0  # nothing follows an error frame
