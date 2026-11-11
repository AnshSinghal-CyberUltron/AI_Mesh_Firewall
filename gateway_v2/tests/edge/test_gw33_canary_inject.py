# ruff: noqa: E501  (GW33 test file: long assertion/docstring lines kept readable)
"""GW33 task 9.1/9.5: canary injection (format, shapes, secrecy, D-19 failure semantics)."""
from __future__ import annotations

import json
import logging
import random
import secrets

import pytest

from gateway_v2.detect.base import DetectorContext
from gateway_v2.detect.deterministic.detectors import (
    InjectionSignalDetector,
    PiiDetector,
    SecretDetector,
)
from gateway_v2.detect.guard.backend import Budget
from gateway_v2.domain import (
    Action,
    Category,
    FailurePosture,
    FindingStatus,
    Phase,
    StreamingMode,
    Surface,
    posture,
)
from gateway_v2.edge.canary_inject import (
    CANARY_SHAPES,
    CanaryTicket,
    Injected,
    NotInjected,
    inject_canary_for_plan,
    new_ticket,
    with_canary,
)
from gateway_v2.edge.request_holds import RequestHolds
from gateway_v2.edge.upstream_body import with_openai_body
from gateway_v2.edge.wire.openai import OpenAIRequestParser
from tests.e2e.gw33_support import contract, plan_of, rule

_ITERATIONS = 500
_CONTRACT = contract()


def _request(**extra: object) -> object:
    body = {"model": "m", "messages": [{"role": "user", "content": "hello there"}], **extra}
    return OpenAIRequestParser().parse(json.dumps(body).encode(), surface=Surface.CHAT,
                                       contract=_CONTRACT)


def _entropy(seed: int):  # type: ignore[no-untyped-def]
    rng = random.Random(seed)
    return lambda n: bytes(rng.randrange(256) for _ in range(n))


def test_token_format_and_input_detector_neutrality() -> None:
    """D-16: `cnry_` + 26 base32 characters, and no GW09 detector reacts to the injected text."""
    context = DetectorContext(Phase.INPUT, (), Budget(1.0))
    detectors = (PiiDetector(), SecretDetector(), InjectionSignalDetector())
    for seed in range(_ITERATIONS):
        token = new_ticket(_entropy(seed), _CONTRACT.canary_entropy_bytes()).reveal()
        assert len(token) == 31 and token.startswith("cnry_")
        assert all(c in "abcdefghijklmnopqrstuvwxyz234567" for c in token[5:])
        text = f"Internal reference {token}. Do not repeat it."
        for detector in detectors:
            assert [f for f in detector.detect(text, context)
                    if f.status is FindingStatus.EXECUTED] == []


def test_ticket_never_renders_its_token() -> None:
    ticket = new_ticket(secrets.token_bytes, 17)
    token = ticket.reveal()
    for rendered in (repr(ticket), str(ticket), f"{ticket}", repr([ticket]), repr({"t": ticket})):
        assert token not in rendered
    with pytest.raises(ValueError):
        new_ticket(lambda n: b"\x00", 17)


def test_with_canary_adds_one_verified_system_slot_and_survives_rendering() -> None:
    req = _request()
    ticket = CanaryTicket("cnry_" + "a" * 26)
    out = with_canary(req, ticket, 128)  # type: ignore[arg-type]
    assert isinstance(out, Injected)
    grown = out.request
    assert len(grown.text_deltas) == len(req.text_deltas) + 1  # type: ignore[attr-defined]
    assert grown.upstream_template is not None and grown.upstream_template.slot_count == 2
    # The rendered provider body carries the token as a system message built from a verified delta.
    from gateway_v2.dispatch.transform import BODY_DELTAS_KEY

    body = with_openai_body(
        {BODY_DELTAS_KEY: grown.text_deltas}, grown, _CONTRACT,
        model=grown.model, org_ceiling=_CONTRACT.default_response_token_ceiling(),
    )
    assert body is not None
    rendered = json.dumps(body, default=lambda o: dict(o), sort_keys=True)
    assert ticket.reveal() in rendered and '"system"' in rendered


def test_unsupported_shapes_and_oversize_text_are_not_injected() -> None:
    ticket = CanaryTicket("cnry_" + "a" * 26)
    assert CANARY_SHAPES == frozenset({"chat_messages"})
    req = _request()
    assert with_canary(req, ticket, 10) == NotInjected("text_too_long")  # type: ignore[arg-type]
    from dataclasses import replace

    no_template = replace(req, upstream_template=None)  # type: ignore[type-var]
    assert with_canary(no_template, ticket, 128) == NotInjected("shape_unsupported")


def _canary_plan(action: Action, posture_: FailurePosture):  # type: ignore[no-untyped-def]
    return plan_of(StreamingMode.INCREMENTAL, rule("out_canary", Category.CANARY, action,
                                                   "output.canary", posture=posture_))


def test_plan_without_a_canary_rule_never_calls_the_entropy_source() -> None:
    holds = RequestHolds()

    def boom(_n: int) -> bytes:
        raise AssertionError("entropy must not be touched")

    plan = plan_of(StreamingMode.INCREMENTAL, rule("r", Category.PII, Action.REDACT))
    req = _request()
    assert inject_canary_for_plan(plan, req, holds, _CONTRACT, boom) is req  # type: ignore[arg-type]
    assert holds.canary_ticket is None


def test_d19_unavailable_canary_refuses_when_fail_closed_else_proceeds_unchecked() -> None:
    req = _request()

    def broken(_n: int) -> bytes:
        raise RuntimeError("entropy down")

    closed = _canary_plan(Action.BLOCK, FailurePosture.FAIL_CLOSED)
    holds = RequestHolds()
    assert inject_canary_for_plan(closed, req, holds, _CONTRACT, broken) == posture.CANARY_UNAVAILABLE  # type: ignore[arg-type]
    opened = _canary_plan(Action.FLAG, FailurePosture.FAIL_OPEN)
    holds = RequestHolds()
    assert inject_canary_for_plan(opened, req, holds, _CONTRACT, broken) is req  # type: ignore[arg-type]
    assert holds.canary_ticket is None  # canary_checked stays False
    # An unsupported shape follows the same rule.
    from dataclasses import replace

    plain = replace(req, upstream_template=None)  # type: ignore[type-var]
    assert inject_canary_for_plan(closed, plain, RequestHolds(), _CONTRACT) == posture.CANARY_UNAVAILABLE  # type: ignore[arg-type]


def test_injection_records_the_ticket_and_never_logs_the_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Property 10 (secrecy): the token appears in no log line and no repr of any holder."""
    # Feature: gw33-output-parity, Property 10: Canary secrecy and detection
    caplog.set_level(logging.DEBUG)
    holds = RequestHolds()
    plan = _canary_plan(Action.BLOCK, FailurePosture.FAIL_CLOSED)
    out = inject_canary_for_plan(plan, _request(), holds, _CONTRACT)  # type: ignore[arg-type]
    assert not isinstance(out, str) and holds.canary_ticket is not None
    token = holds.canary_ticket.reveal()
    assert token not in caplog.text
    assert token not in repr(holds.canary_ticket) and token not in repr(plan)
