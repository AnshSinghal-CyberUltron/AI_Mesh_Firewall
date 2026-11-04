"""GW36 S1: the request the provider receives is rebuilt from VERIFIED deltas, with its structure.

Two halves:

* wire properties (seeded ``random.Random``, >= 10,000 iterations, house idiom): the fold and the
  upstream template are built by one loop, so slot ``i`` is pair ``i``; the template carries no
  request text; rendering with the original texts reproduces every forwarded text; and
* the dev-profile pipeline over raw ASGI with a provider that records the RENDERED request
  (``openai_body``): roles survive, PII is masked in what the provider is sent, ``prompt`` and
  ``stop`` are scanned, the v1 output-budget rule holds, refused fields make zero provider calls,
  and a gateway-detected upstream failure is an error, never a 200.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from gateway_v2.dispatch.openai_upstream import BODY_ENDPOINT_KEY, BODY_OPENAI_KEY
from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.transform import BODY_DELTAS_KEY
from gateway_v2.domain.plan import Surface
from gateway_v2.edge import serve
from gateway_v2.edge.upstream_body import with_openai_body
from gateway_v2.edge.wire.openai import (
    MALFORMED_WRONG_SHAPE,
    UNSUPPORTED_CONTENT_PART,
    UNSUPPORTED_PARAMETER,
    MalformedRequest,
    OpenAIRequestParser,
)
from gateway_v2.edge.wire.openai_fold import TextSlot, UpstreamTemplate
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

EXAMPLE_PLAN = Path(__file__).resolve().parents[2] / "dev" / "plan.example.json"
SSN = "123-45-6789"
KEY = "sk-gw36-s1"
_SEED = 0x36_51
_ITERATIONS = 10_000
_ROLES = ("system", "developer", "user", "assistant")
_ALPHABET = "abcdefghij klmnop?!.-_0123456789"


def _contract() -> ResourceContract:
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=400 * 1024 * 1024 * 8,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals, target_p99_ms=20.0, utilization_cap=0.75, per_worker_rss=400 * 1024 * 1024
    )


def _parse(obj: Mapping[str, object]) -> Any:
    return OpenAIRequestParser().parse(
        json.dumps(obj).encode(), surface=Surface.CHAT, contract=_contract()
    )


# --------------------------------------------------------------------------- #
# Wire properties.
# --------------------------------------------------------------------------- #


def _text(rng: random.Random) -> str:
    return "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(0, 12)))


def _random_body(rng: random.Random) -> dict[str, object]:
    body: dict[str, object] = {"model": "m-1"}
    if rng.random() < 0.2:
        body["prompt"] = _text(rng) if rng.random() < 0.5 else [_text(rng)]
    else:
        messages = []
        for _ in range(rng.randint(0, 5)):
            message: dict[str, object] = {"role": rng.choice(_ROLES)}
            shape = rng.random()
            if shape < 0.5:
                message["content"] = _text(rng)
            elif shape < 0.9:
                message["content"] = [
                    {"type": "text", "text": _text(rng)} for _ in range(rng.randint(0, 3))
                ]
            else:
                message["content"] = None
            messages.append(message)
        body["messages"] = messages
    if rng.random() < 0.3:
        body["stop"] = _text(rng) if rng.random() < 0.5 else [_text(rng), _text(rng)]
    return body


def _template_strings(template: UpstreamTemplate) -> list[str]:
    strings = [message.role for message in template.messages]
    for message in template.messages:
        content = message.content
        slots = (content,) if isinstance(content, TextSlot) else content or ()
        strings.extend(slot.channel for slot in slots)
    return strings


def _expected_content(raw: object) -> object:
    if isinstance(raw, list):
        return "".join(str(part["text"]) for part in raw)
    return raw


def test_property_template_slots_align_with_the_fold_and_carry_no_text() -> None:
    rng = random.Random(_SEED)
    for i in range(_ITERATIONS):
        body = _random_body(rng)
        req = _parse(body)
        template = req.upstream_template
        assert template is not None, (_SEED, i)
        assert template.slot_count == len(req.input_channels), (_SEED, i, body)
        texts = {text for _channel, text in req.input_channels if text}
        roles = set(_ROLES)
        leaked = [s for s in _template_strings(template) if s in texts and s not in roles]
        assert not leaked, (_SEED, i, body, leaked)
        rendered = with_openai_body(
            MappingProxyType({BODY_DELTAS_KEY: req.text_deltas}), req, _contract(),
            model=req.model, org_ceiling=_contract().default_response_token_ceiling(),
        )
        assert rendered is not None, (_SEED, i, body)
        sent = rendered[BODY_OPENAI_KEY]
        assert isinstance(sent, Mapping)
        if "prompt" in body:
            prompt = body["prompt"]
            want = prompt if isinstance(prompt, str) else prompt[0]  # type: ignore[index]
            assert [dict(m) for m in sent["messages"]] == [  # type: ignore[union-attr]
                {"role": "user", "content": want}
            ], (_SEED, i)
        else:
            got = [dict(m) for m in sent["messages"]]  # type: ignore[union-attr]
            want_messages = [
                {"role": m["role"], "content": _expected_content(m["content"])}
                for m in body["messages"]  # type: ignore[union-attr]
            ]
            assert got == want_messages, (_SEED, i, body)
        if "stop" in body:
            stop = body["stop"]
            assert sent["stop"] == (stop if isinstance(stop, str) else tuple(stop)), (_SEED, i)


def test_a_slot_whose_delta_moved_channel_is_refused() -> None:
    req = _parse({"model": "m", "messages": [{"role": "user", "content": "hi"}]})
    moved = MappingProxyType({BODY_DELTAS_KEY: (("content_part_text", "hi"),)})
    ceiling = _contract().default_response_token_ceiling()
    kw = {"model": req.model, "org_ceiling": ceiling}
    assert with_openai_body(moved, req, _contract(), **kw) is None
    short = MappingProxyType({BODY_DELTAS_KEY: ()})
    assert with_openai_body(short, req, _contract(), **kw) is None


@pytest.mark.parametrize(
    ("body", "code", "param"),
    [
        ({"tools": []}, UNSUPPORTED_PARAMETER, "tools"),
        ({"tool_choice": "auto"}, UNSUPPORTED_PARAMETER, "tool_choice"),
        ({"response_format": {"type": "json_object"}}, UNSUPPORTED_PARAMETER, "response_format"),
        ({"logprobs": True}, UNSUPPORTED_PARAMETER, "logprobs"),
        ({"functions": []}, UNSUPPORTED_PARAMETER, "functions"),
        ({"not a field!": 1}, UNSUPPORTED_PARAMETER, None),
        ({"temperature": 3.0}, MALFORMED_WRONG_SHAPE, "temperature"),
        ({"temperature": "123-45-6789"}, MALFORMED_WRONG_SHAPE, "temperature"),
        ({"reasoning_effort": "maximal"}, MALFORMED_WRONG_SHAPE, "reasoning_effort"),
        ({"logit_bias": {"abc": 1}}, MALFORMED_WRONG_SHAPE, "logit_bias"),
        ({"logit_bias": {"²": 1}}, MALFORMED_WRONG_SHAPE, "logit_bias"),
        ({"stop": ["a", "b", "c", "d", "e"]}, MALFORMED_WRONG_SHAPE, "stop"),
        ({"model": "gpt 4 ssn 123-45-6789"}, MALFORMED_WRONG_SHAPE, "model"),
    ],
)
def test_fields_the_gateway_does_not_scan_or_type_are_refused(
    body: dict[str, object], code: str, param: str | None
) -> None:
    request = {"model": "m", "messages": [{"role": "user", "content": "hi"}], **body}
    with pytest.raises(MalformedRequest) as refused:
        _parse(request)
    assert (refused.value.code, refused.value.param) == (code, param)


@pytest.mark.parametrize(
    ("messages", "code"),
    [
        ([{"role": "tool", "content": "x", "tool_call_id": "c1"}], UNSUPPORTED_PARAMETER),
        ([{"role": "function", "content": "x", "name": "f"}], UNSUPPORTED_PARAMETER),
        (
            [{"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]}],
            UNSUPPORTED_PARAMETER,
        ),
        ([{"role": "user", "content": [{"type": "image_url", "image_url": {}}]}],
         UNSUPPORTED_CONTENT_PART),
        ([{"role": "narrator", "content": "x"}], UNSUPPORTED_PARAMETER),
    ],
)
def test_message_shapes_the_gateway_does_not_forward_are_refused(
    messages: list[dict[str, object]], code: str
) -> None:
    with pytest.raises(MalformedRequest) as refused:
        _parse({"model": "m", "messages": messages})
    assert (refused.value.code, refused.value.param) == (code, "messages")


@pytest.mark.parametrize(
    "body",
    [
        {"prompt": ["a", "b"]},
        {"prompt": [1, 2, 3]},
        {"prompt": "x", "messages": [{"role": "user", "content": "y"}]},
    ],
)
def test_prompt_shapes_that_cannot_be_one_scanned_turn_are_refused(
    body: dict[str, object],
) -> None:
    with pytest.raises(MalformedRequest) as refused:
        _parse({"model": "m", **body})
    assert (refused.value.code, refused.value.param) == (UNSUPPORTED_PARAMETER, "prompt")


def test_a_message_name_is_accepted_but_not_forwarded() -> None:
    req = _parse({"model": "m", "messages": [{"role": "user", "content": "hi", "name": "AKIA"}]})
    rendered = with_openai_body(
        MappingProxyType({BODY_DELTAS_KEY: req.text_deltas}), req, _contract(),
        model=req.model, org_ceiling=_contract().default_response_token_ceiling(),
    )
    assert rendered is not None
    assert "AKIA" not in json.dumps(_plain(rendered[BODY_OPENAI_KEY]))


@pytest.mark.parametrize(
    ("budget", "expected"),
    [
        ({}, {"max_tokens": 4096}),
        ({"max_tokens": 0}, {"max_tokens": 4096}),
        ({"max_tokens": 100}, {"max_tokens": 100}),
        ({"max_tokens": 999_999}, {"max_tokens": 4096}),
        ({"max_completion_tokens": 50}, {"max_completion_tokens": 50}),
        ({"max_completion_tokens": 900_000}, {"max_completion_tokens": 4096}),
    ],
)
def test_output_budget_follows_v1(budget: dict[str, int], expected: dict[str, int]) -> None:
    from gateway_v2.edge.wire.openai import clamp_output_budget

    contract = _contract()
    req = clamp_output_budget(
        _parse({"model": "m", "messages": [{"role": "user", "content": "hi"}], **budget}),
        org_ceiling=contract.default_response_token_ceiling(),
        contract=contract,
    )
    rendered = with_openai_body(
        MappingProxyType({BODY_DELTAS_KEY: req.text_deltas}),
        req,
        contract,
        model=req.model,
        org_ceiling=contract.default_response_token_ceiling(),
    )
    assert rendered is not None
    sent = rendered[BODY_OPENAI_KEY]
    got = {k: sent[k] for k in ("max_tokens", "max_completion_tokens") if k in sent}  # type: ignore[operator, index]
    assert got == expected


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(v) for v in value]
    return value


# --------------------------------------------------------------------------- #
# The dev-profile pipeline.
# --------------------------------------------------------------------------- #


class _Recorder:
    """Records the rendered request; replies with one content event, or a scripted failure."""

    def __init__(self, failure: str | None = None) -> None:
        self.sent: list[Mapping[str, object]] = []
        self.failure = failure

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        return self._stream(request)

    async def _stream(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        assert request.body[BODY_ENDPOINT_KEY] == "chat/completions"
        body = request.body[BODY_OPENAI_KEY]
        assert isinstance(body, Mapping)
        self.sent.append(_plain(body))  # type: ignore[arg-type]
        if self.failure is not None:
            yield UpstreamEvent(text_deltas=(), final=True, gateway_error=self.failure)
            return
        yield UpstreamEvent(text_deltas=(("content", "ok"),), final=True)

    async def abort(self) -> None:
        return None


def _app(provider: _Recorder) -> object:
    env = {
        "AMF_DEV_PROFILE": "1",
        "AMF_DEV_API_KEY": KEY,
        "AMF_DEV_PLAN_PATH": str(EXAMPLE_PLAN),
        "AMF_DEV_MODELS": "echo-1",
        "AMF_DEV_Q_SAFE": "500",
        "AMF_DEV_AUDIT_DRAIN_RATE_PER_S": "1000",
        "AMF_DEV_AUDIT_BYTES_PER_RECORD": "2765",
        "AMF_DEV_BUDGET": "unlimited",
    }
    deps, _logs = serve.build_boot_dependencies(contract=_contract(), env=env)
    deps["provider"] = provider
    return serve._assemble_app(deps)


async def _call(app: object, path: str, payload: Mapping[str, object]) -> tuple[int, bytes]:
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [(b"authorization", f"Bearer {KEY}".encode()), (b"content-type", b"json")],
    }
    sent: list[Mapping[str, object]] = []
    delivered = False

    async def receive() -> Mapping[str, object]:
        nonlocal delivered
        if delivered:
            await asyncio.sleep(3600)
        delivered = True
        return {"type": "http.request", "body": json.dumps(payload).encode(), "more_body": False}

    async def send(message: Mapping[str, object]) -> None:
        sent.append(message)

    await app(scope, receive, send)  # type: ignore[operator]
    status = next(int(m["status"]) for m in sent if m["type"] == "http.response.start")  # type: ignore[call-overload]
    body = b"".join(bytes(m.get("body", b"")) for m in sent if m["type"] == "http.response.body")  # type: ignore[arg-type]
    return status, body


def _chat(provider: _Recorder, payload: Mapping[str, object], path: str = "/v1/chat/completions"):  # type: ignore[no-untyped-def]
    return asyncio.run(_call(_app(provider), path, {"model": "echo-1", **payload}))


def test_roles_survive_and_pii_is_masked_in_what_the_provider_is_sent() -> None:
    provider = _Recorder()
    messages = [
        {"role": "system", "content": "You are terse."},
        {"role": "user", "content": f"my ssn is {SSN}"},
        {"role": "assistant", "content": "noted"},
        {
            "role": "user",
            "content": [{"type": "text", "text": "and "}, {"type": "text", "text": "go"}],
        },
    ]
    status, _body = _chat(provider, {"messages": messages})
    assert status == 200
    [sent] = provider.sent
    assert [m["role"] for m in sent["messages"]] == ["system", "user", "assistant", "user"]  # type: ignore[index]
    wire = json.dumps(sent)
    assert SSN not in wire and "[REDACTED]" in wire
    assert sent["messages"][3]["content"] == "and go"  # type: ignore[index]
    assert sent["max_tokens"] == 4096 and sent["stream"] is True


def test_a_legacy_prompt_is_scanned_and_sent_as_one_user_turn() -> None:
    provider = _Recorder()
    status, _body = _chat(provider, {"prompt": f"ssn {SSN}"}, path="/v1/completions")
    assert status == 200
    [sent] = provider.sent
    assert sent["messages"] == [{"role": "user", "content": "ssn [REDACTED]"}]


def test_stop_strings_are_scanned_before_they_are_forwarded() -> None:
    provider = _Recorder()
    status, _body = _chat(
        provider, {"messages": [{"role": "user", "content": "hi"}], "stop": [f"x {SSN}", "END"]}
    )
    assert status == 200
    [sent] = provider.sent
    assert SSN not in json.dumps(sent)
    assert sent["stop"][1] == "END"  # type: ignore[index]


@pytest.mark.parametrize(
    "extra",
    [
        {"tools": [{"type": "function", "function": {"name": "f"}}]},
        {"response_format": {"type": "json_object"}},
        {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {}}]}]},
    ],
)
def test_a_refused_field_makes_zero_provider_calls(extra: dict[str, object]) -> None:
    provider = _Recorder()
    payload = {"messages": [{"role": "user", "content": "hi"}], **extra}
    status, body = _chat(provider, payload)
    assert status == 400, body
    assert provider.sent == []


@pytest.mark.parametrize(
    ("failure", "status"),
    [
        ("upstream_rate_limited", 429),
        ("upstream_invalid_request", 400),
        ("upstream_auth_error", 502),
        ("upstream_unavailable", 502),
        ("malformed_upstream", 502),
    ],
)
def test_a_gateway_detected_upstream_failure_is_an_error_never_a_200(
    failure: str, status: int
) -> None:
    from gateway_v2.domain.posture import STREAM_MALFORMED_UPSTREAM

    code = STREAM_MALFORMED_UPSTREAM if failure == "malformed_upstream" else failure
    provider = _Recorder(failure=code)
    got_status, body = _chat(provider, {"messages": [{"role": "user", "content": "hi"}]})
    assert got_status == status, body
    assert json.loads(body)["error"]["code"] == code


def test_a_streamed_upstream_failure_ends_with_the_declared_error_frame() -> None:
    provider = _Recorder(failure="upstream_rate_limited")
    status, body = _chat(
        provider, {"messages": [{"role": "user", "content": "hi"}], "stream": True}
    )
    text = body.decode()
    assert status == 200  # the SSE start precedes provider.open (recorded S5 / GW34 follow-up)
    assert "upstream_rate_limited" in text
    assert '"content":"' not in text.replace('"content":""', "")
    trace = next(
        json.loads(line[len("data: ") :])
        for line in text.splitlines()
        if line.startswith("data: ") and "pipeline_trace" in line
    )
    assert trace["pipeline_trace"]["final_action"] != "allow", trace
