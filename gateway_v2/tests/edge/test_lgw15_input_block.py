# Feature: chat-surface-gw15, Property 9: A BLOCK input-channel finding yields zero provider calls (input channel test)  # noqa: E501
# Validates: Requirements 7.1, 7.2, 7.3
"""Property 9 — a BLOCK input-channel finding refuses the request with zero provider calls.

House idiom: a seeded ``random.Random``, no ``hypothesis``, >=10,000 iterations; the
``ChatSurface.run_lifecycle`` coroutine is driven via ``asyncio.run``.

For any well-formed SDK request and *any* input ``TextChannel``, a canary that the
injected detector resolves to a BLOCK disposition (C22 ``most_restrictive``) causes the
§10.4 lifecycle to:

* refuse the request before the provider is opened (R7.1 / R7.2) — the counted
  :class:`~gateway_v2.dispatch.provider.StubProviderClient` sees ZERO ``open`` calls, and
* render the declared block value-code
  (:data:`~gateway_v2.edge.openai_chat.REFUSE_INPUT_BLOCKED`, R7.3).

The canary is iterated over EVERY input ``TextChannel`` entry (the four ``messages[]``-
scoped names, derived from the authoritative ``TEXT_CHANNELS`` tuple) each iteration, so a
dropped folding path for ANY single channel — a channel whose canary never reaches the
input scan — fails the gate (C21).

This file also hosts Property 10 (task 2.10); every symbol this test owns is prefixed
``_P9_`` / ``_p9_`` / ``P9_`` and the section is additive so the two coexist without a
name clash.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from gateway_v2.admit.grant import Admitted
from gateway_v2.dispatch.provider import StubProviderClient, UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.routing import DispatchRouter
from gateway_v2.dispatch.transform import Transform
from gateway_v2.domain.category import Category
from gateway_v2.domain.channels import TEXT_CHANNELS
from gateway_v2.domain.finding import Finding, FindingStatus, Span
from gateway_v2.domain.identity import Principal
from gateway_v2.domain.plan import ExecutionPlan, StreamingMode
from gateway_v2.edge.identity_resolver import TenantIdentity
from gateway_v2.edge.openai_chat import REFUSE_INPUT_BLOCKED, ChatSurface
from gateway_v2.edge.wire.openai import OpenAIRequestParser
from gateway_v2.egress.output_guard import MinimalOutputResolver
from gateway_v2.plan.snapshot import PlanState as _PlanState

if TYPE_CHECKING:
    from gateway_v2.edge.routes import ChatRoute

_P9_SEED = 0x6C_6757_0009
_P9_ITERATIONS = 10_000

#: The ``TextChannel.sdk_path`` prefix that marks an INPUT (request-side) channel;
#: mirrors ``edge/wire/openai._INPUT_SDK_PATH_PREFIX`` so the canary iterates over the
#: SAME input-scoped channel set the fold's totality check pins.
_P9_INPUT_SDK_PATH_PREFIX = "messages[]"

#: A BLOCK-class detector name: ``MinimalOutputResolver`` maps ``card`` (and ``jwt``) to
#: ``Disposition.BLOCK`` via its trade-off table, so a finding tagged with this detector
#: resolves to a BLOCK decision regardless of any co-matching redact/flag finding (C22).
_P9_BLOCK_DETECTOR = "card"

#: Alphabet for canary synthesis — printable, SDK-safe, distinctive so a canary that
#: slips past the scan is unambiguous in a counterexample.
_P9_CANARY_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"


def _p9_ctx(seed: int, i: int, **extra: object) -> str:
    body = " ".join(f"{k}={v!r}" for k, v in extra.items())
    return f"seed={seed:#x} iter={i} {body}"


def _p9_canary(rng: random.Random) -> str:
    """A distinctive canary token unlikely to collide with any filler text."""
    n = rng.randint(4, 24)
    return "P9CANARY_" + "".join(rng.choice(_P9_CANARY_ALPHABET) for _ in range(n))


def _p9_input_channel_names() -> tuple[str, ...]:
    """The input-scoped ``TEXT_CHANNELS`` names, from the authoritative enumeration."""
    return tuple(
        channel.name
        for channel in TEXT_CHANNELS
        if channel.sdk_path.startswith(_P9_INPUT_SDK_PATH_PREFIX)
    )


def _p9_request_with_canary(channel_name: str, canary: str) -> dict[str, object]:
    """A well-formed OpenAI request body placing ``canary`` in exactly ``channel_name``.

    One message carries the canary in the field the named input ``TextChannel`` maps to;
    the request is otherwise minimal and well-formed so the parser accepts it and the fold
    walks it. A new input channel with no placement here raises, so the test cannot
    silently skip a channel (C21).
    """
    if channel_name == "message_content":
        message: dict[str, object] = {"role": "user", "content": canary}
    elif channel_name == "content_part_text":
        message = {"role": "user", "content": [{"type": "text", "text": canary}]}
    elif channel_name == "tool_arguments":
        message = {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_0",
                    "type": "function",
                    "function": {"name": "f", "arguments": canary},
                }
            ],
        }
    elif channel_name == "function_arguments":
        message = {
            "role": "assistant",
            "function_call": {"name": "f", "arguments": canary},
        }
    else:  # pragma: no cover - guards a new input channel with no case here (C21)
        raise AssertionError(f"no canary placement for input channel {channel_name!r}")
    return {"model": "gpt-p9", "messages": [message]}


# --------------------------------------------------------------------------- #
# Stubs — a counted provider + the minimal fail-closed deps the lifecycle reads.
# --------------------------------------------------------------------------- #


class _P9CountingProvider(StubProviderClient):
    """A :class:`StubProviderClient` that counts every ``open`` so the test can assert zero.

    Property 9 is "zero provider calls": the counter increments on each ``open`` and the
    test asserts it never moved on a BLOCK-refused request. It is a real ``StubProviderClient``
    (no socket) so the deps bundle holds exactly what production wires.
    """

    __slots__ = ("open_calls",)

    def __init__(self, *, rng: random.Random) -> None:
        super().__init__(script=(UpstreamEvent(text_deltas=(), final=True),), rng=rng)
        self.open_calls = 0

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        self.open_calls += 1
        return super().open(request)


class _P9BoundContract:
    """A tiny ``ResourceContract`` stand-in exposing only the three pre-check accessors.

    The §10.4 pre-identity clock reads ``max_request_bytes`` / ``max_expansion_ratio`` /
    ``max_request_parts`` from the injected contract. Property 9 is about the INPUT-scan
    BLOCK short-circuit, not the pre-checks, so the bounds are generous enough that every
    well-formed canary request clears them and the lifecycle reaches the input scan.
    """

    __slots__ = ()

    @staticmethod
    def max_request_bytes() -> int:
        return 1_000_000

    @staticmethod
    def max_expansion_ratio() -> float:
        return 100.0

    @staticmethod
    def max_request_parts() -> int:
        return 1_000_000

    @staticmethod
    def max_output_tokens_boundary() -> int:
        """v1's ``max_tokens`` parse boundary; ``parse`` reads it to validate token fields."""
        return 1_000_000

    @staticmethod
    def default_response_token_ceiling() -> int:
        """v1's default org output ceiling; ``run_lifecycle`` clamps ``max_tokens`` to it."""
        return 4096

    @staticmethod
    def max_output_tokens() -> int:
        """v1's output-token hard cap (the clamp's upper bound)."""
        return 65536

    @staticmethod
    def max_completions() -> int:
        """v1's completion cap; the clamp forces ``n`` to it."""
        return 1

    @staticmethod
    def estimate_request_tokens(chars: int, max_tokens: int, n: int) -> int:
        """v1's request token estimate the budget phase charges over the clamped request."""
        input_tokens = max(1, chars // 4) if chars > 0 else 0
        return max(input_tokens + max_tokens * n, 1)


class _P9Identity:
    """A fixed ``IdentityResolver`` (see ``edge/identity_resolver.py``) — resolves one tenant."""

    __slots__ = ("_identity",)

    def __init__(self, identity: TenantIdentity) -> None:
        self._identity = identity

    async def resolve(self, scope: Mapping[str, object]) -> TenantIdentity | None:
        del scope
        return self._identity


class _P9Snapshot:
    """A snapshot whose ``lookup`` always returns the one canned plan (R2.1 / R3.3)."""

    __slots__ = ("_plan",)

    def __init__(self, plan: ExecutionPlan) -> None:
        self._plan = plan

    def lookup(self, org_id: str, now: float | None = None) -> _PlanState:
        del org_id, now
        return self._plan


class _P9Admission:
    """Always admits (so the lifecycle reaches the input scan, R5.3)."""

    __slots__ = ()

    async def admit(self, *, owner_id: str, request_id: str, enqueued_at: float) -> Admitted:
        return Admitted(owner_id=owner_id, request_id=request_id, admitted_at=enqueued_at)

    def complete(self, request_id: str) -> None:
        del request_id


class _P9Quota:
    """Always within budget (so the lifecycle reaches the input scan, R6.1)."""

    __slots__ = ()

    async def evaluate(self, cost: int, *, request_id: str) -> Admitted:
        del cost
        return Admitted(owner_id="p9", request_id=request_id, admitted_at=0.0)

    def refund(self, cost: int, *, request_id: str) -> None:
        del cost, request_id


class _P9BlockDetector:
    """Injected input detector: a BLOCK-class finding on the canary, nothing otherwise.

    The detector is the ``Callable[[str], Sequence[Finding]]`` seam injected in ``edge``
    (above ``detect``, R7.5). It emits one EXECUTED finding tagged with the BLOCK-class
    detector name whenever the canary substring is present, so a canary in ANY folded
    input channel produces a BLOCK finding; all other (filler) text produces none.
    """

    __slots__ = ("_canary",)

    def __init__(self, canary: str) -> None:
        self._canary = canary

    def __call__(self, text: str) -> Sequence[Finding]:
        if self._canary not in text:
            return ()
        start = text.index(self._canary)
        return (
            Finding(
                detector=_P9_BLOCK_DETECTOR,
                detector_version="p9",
                category=Category.SECRET,
                status=FindingStatus.EXECUTED,
                confidence=1.0,
                spans=(Span(start=start, end=start + len(self._canary)),),
                evidence=None,
            ),
        )


@dataclass(slots=True)
class _P9Deps:
    """The minimal duck-typed deps bundle ``ChatSurface`` reads up to the input scan.

    ``ChatSurface(deps=...)`` only ever reads attributes off ``deps``; this bundle carries
    exactly the ones the pre-provider lifecycle touches (contract / parser / identity /
    snapshot_source / admission / quota / clock / input_detector / resolver) plus the
    proceed-path seams (router / transform) so a non-blocked control request can build its
    outcome. Every seam is fail-closed-or-permissive by construction, isolating the one
    behaviour under test: the INPUT-scan BLOCK short-circuit.
    """

    contract: _P9BoundContract
    parser: OpenAIRequestParser
    identity: _P9Identity
    snapshot_source: object
    admission: _P9Admission
    quota: _P9Quota
    clock: object
    input_detector: _P9BlockDetector
    resolver: MinimalOutputResolver
    router: DispatchRouter
    transform: Transform
    #: The counted provider is REACHABLE on the deps (as production wires it) but the
    #: pre-provider lifecycle never opens it — the test asserts its call count stayed zero.
    provider: _P9CountingProvider


def _p9_plan() -> ExecutionPlan:
    """A minimal resolved :class:`ExecutionPlan` for the one test tenant."""
    return ExecutionPlan(
        org_id="p9-org",
        epoch=1,
        sequence=1,
        content_hash="p9" * 16,
        compiled_at=0.0,
        rules=(),
        required_detectors=frozenset(),
        streaming_mode=StreamingMode.INCREMENTAL,
        integrity_locked=False,
    )


def _p9_build_deps(canary: str, provider: _P9CountingProvider) -> _P9Deps:
    """Build the fail-closed-or-permissive deps bundle for one canary request.

    The counted ``provider`` is REACHABLE on the bundle (as production wires it onto
    ``ChatRoute``), so the zero-call assertion is a real structural check: the pre-provider
    lifecycle has the provider available and still never opens it — the Property-9 invariant.
    """
    plan = _p9_plan()
    identity = TenantIdentity(org_id="p9-org", principal=_p9_principal())
    return _P9Deps(
        contract=_P9BoundContract(),
        parser=OpenAIRequestParser(),
        identity=_P9Identity(identity),
        snapshot_source=lambda: _P9Snapshot(plan),
        admission=_P9Admission(),
        quota=_P9Quota(),
        clock=lambda: 0.0,
        input_detector=_P9BlockDetector(canary),
        resolver=MinimalOutputResolver(),
        router=DispatchRouter(),
        transform=Transform(),
        provider=provider,
    )


def _p9_principal() -> Principal:
    return Principal(
        key_id="p9-key",
        org_id="p9-org",
        rate_per_s=1.0,
        burst=1.0,
        feed_seq=0,
    )


def test_property_9_block_input_channel_yields_zero_provider_calls() -> None:
    rng = random.Random(_P9_SEED)
    input_channels = _p9_input_channel_names()

    # Coverage guard: every input-scoped TEXT_CHANNELS entry must have a canary placement
    # below, so a new input channel without a case fails the gate (C21).
    for name in input_channels:
        _p9_request_with_canary(name, "P9CANARY_probe")
    assert input_channels, _p9_ctx(_P9_SEED, -1, note="no input channels enumerated")

    for i in range(_P9_ITERATIONS):
        # Iterate the canary over EVERY input TextChannel entry each iteration so a dropped
        # folding path for ANY single channel (its canary never reaching the scan) fails.
        for channel_name in input_channels:
            canary = _p9_canary(rng)
            provider = _P9CountingProvider(rng=rng)
            deps = _p9_build_deps(canary, provider)
            # The lifecycle reads only the attributes this bundle supplies; cast to the
            # declared ``ChatRoute`` deps type so ``mypy --strict`` sees the structural match.
            surface = ChatSurface(deps=cast("ChatRoute", deps))
            body = json.dumps(_p9_request_with_canary(channel_name, canary)).encode("utf-8")
            scope: Mapping[str, object] = {"path": "/v1/chat/completions"}

            req, outcome = asyncio.run(surface.run_lifecycle(scope, body))

            # The request parsed (so the refusal is the INPUT-scan BLOCK, not a parse error)
            # and the canary folded into THIS channel (C21 — the scan actually saw it).
            assert req is not None, _p9_ctx(
                _P9_SEED, i, note="request did not parse", channel=channel_name
            )
            assert (channel_name, canary) in req.input_channels, _p9_ctx(
                _P9_SEED,
                i,
                note="canary not folded into its channel (scan could not see it)",
                channel=channel_name,
                canary=canary,
                folded=req.input_channels,
            )

            # R7.2 / R7.1: the request is refused and ZERO provider calls were made.
            assert outcome.refuse_code is not None, _p9_ctx(
                _P9_SEED, i, note="BLOCK canary did not refuse", channel=channel_name
            )
            assert provider.open_calls == 0, _p9_ctx(
                _P9_SEED,
                i,
                note="provider was opened on a BLOCK-refused request",
                channel=channel_name,
                open_calls=provider.open_calls,
            )
            # The proceed-path provider inputs are withheld on a refusal (fail closed).
            assert outcome.plan is None and outcome.destination is None and outcome.body is None, (
                _p9_ctx(_P9_SEED, i, note="refusal leaked a provider input", channel=channel_name)
            )

            # R7.3: the refusal renders the declared block value-code.
            assert outcome.refuse_code == REFUSE_INPUT_BLOCKED, _p9_ctx(
                _P9_SEED,
                i,
                note="refuse_code is not the declared input-block code",
                channel=channel_name,
                refuse_code=outcome.refuse_code,
            )


# Feature: chat-surface-gw15, Property 10: BLOCK is the most-restrictive disposition (C22)  # noqa: E501
# Validates: Requirements 7.4
# --------------------------------------------------------------------------- #
# Property 10 — BLOCK is the most-restrictive disposition (C22).
#
# For any set of findings on a request that contains at least one BLOCK-disposition
# finding, the resolved outcome is BLOCK regardless of any co-matching REDACT/FLAG/ALLOW
# finding. This exercises the shipped ``MinimalOutputResolver`` directly: it maps each
# finding to a per-finding disposition and composes them with ``most_restrictive`` (C22),
# so a single BLOCK finding dominates every co-matching lesser disposition.
#
# House idiom: a seeded ``random.Random``, no ``hypothesis``, >=10,000 iterations. This
# section is additive to the Property 9 section above; every symbol it owns is prefixed
# ``_p10_`` / ``_P10_`` / ``P10_`` so the two coexist without a name clash.
# --------------------------------------------------------------------------- #

from gateway_v2.domain.decision import Disposition  # noqa: E402

_P10_SEED = 0x6C_6757_0010
_P10_ITERATIONS = 10_000

#: Detector names ``MinimalOutputResolver`` maps to ``Disposition.BLOCK`` via its
#: trade-off table (``jwt``/``card`` are the terminate-the-stream classes, C22).
_P10_BLOCK_DETECTORS: tuple[str, ...] = ("card", "jwt")

#: Detector names the resolver maps to ``Disposition.REDACT`` — the redact-the-remainder
#: classes. A co-matching REDACT finding must NOT soften the BLOCK outcome.
_P10_REDACT_DETECTORS: tuple[str, ...] = (
    "aws",
    "api_token",
    "email",
    "uuid",
    "sha256",
    "url",
    "base64",
)

#: An unknown executed detector the resolver redacts by default (fail-safe REDACT); it is
#: another non-BLOCK co-matcher that must not override the BLOCK finding.
_P10_UNKNOWN_REDACT_DETECTOR = "p10-unknown-detector"


def _p10_ctx(seed: int, i: int, **extra: object) -> str:
    body = " ".join(f"{k}={v!r}" for k, v in extra.items())
    return f"seed={seed:#x} iter={i} {body}"


def _p10_executed_finding(detector: str, offset: int) -> Finding:
    """A single EXECUTED finding tagged with ``detector`` at a disjoint span."""
    return Finding(
        detector=detector,
        detector_version="p10",
        category=Category.SECRET,
        status=FindingStatus.EXECUTED,
        confidence=1.0,
        spans=(Span(start=offset, end=offset + 4),),
        evidence=None,
    )


def _p10_non_executed_finding(detector: str) -> Finding:
    """A SKIPPED finding — resolves to ALLOW, a lesser co-matcher that must not win."""
    return Finding(
        detector=detector,
        detector_version="p10",
        category=Category.SECRET,
        status=FindingStatus.SKIPPED,
        confidence=None,
        spans=(),
        evidence=None,
    )


def test_property_10_block_is_most_restrictive_disposition() -> None:
    rng = random.Random(_P10_SEED)
    resolver = MinimalOutputResolver()

    for i in range(_P10_ITERATIONS):
        offset = 0
        findings: list[Finding] = []

        # >=1 guaranteed BLOCK finding (C22's precondition).
        n_block = rng.randint(1, 3)
        for _ in range(n_block):
            findings.append(
                _p10_executed_finding(rng.choice(_P10_BLOCK_DETECTORS), offset)
            )
            offset += 8

        # Co-matching lesser dispositions: REDACT (known + unknown-default) and ALLOW
        # (non-EXECUTED). None of these may soften the resolved BLOCK outcome.
        for _ in range(rng.randint(0, 5)):
            kind = rng.random()
            if kind < 0.5:
                findings.append(
                    _p10_executed_finding(rng.choice(_P10_REDACT_DETECTORS), offset)
                )
            elif kind < 0.75:
                findings.append(
                    _p10_executed_finding(_P10_UNKNOWN_REDACT_DETECTOR, offset)
                )
            else:
                findings.append(_p10_non_executed_finding("p10-skipped"))
            offset += 8

        # Shuffle so BLOCK is not positionally privileged — order must not matter (C22).
        rng.shuffle(findings)

        decision = resolver.decide(findings)

        # The overall resolved disposition is BLOCK regardless of co-matchers (R7.4).
        assert decision.disposition is Disposition.BLOCK, _p10_ctx(
            _P10_SEED,
            i,
            note="overall disposition was not BLOCK despite a BLOCK finding",
            disposition=decision.disposition,
            per_finding=decision.per_finding,
        )

        # Every BLOCK-class finding resolved to a per-finding BLOCK disposition, and at
        # least one such per-finding BLOCK exists (the C22 precondition is realised).
        block_detectors = set(_P10_BLOCK_DETECTORS)
        per_block = [
            fd for fd in decision.per_finding if fd.detector in block_detectors
        ]
        assert per_block, _p10_ctx(
            _P10_SEED, i, note="no BLOCK-class per-finding disposition present"
        )
        for fd in per_block:
            assert fd.disposition is Disposition.BLOCK, _p10_ctx(
                _P10_SEED,
                i,
                note="a BLOCK-class finding did not resolve to BLOCK",
                detector=fd.detector,
                disposition=fd.disposition,
            )

        # No co-matching finding was dropped: per-finding count matches input count, so
        # the BLOCK wins by composition (most_restrictive), not by discarding co-matchers.
        assert len(decision.per_finding) == len(findings), _p10_ctx(
            _P10_SEED,
            i,
            note="per-finding count diverged from input findings",
            per_finding=len(decision.per_finding),
            inputs=len(findings),
        )
