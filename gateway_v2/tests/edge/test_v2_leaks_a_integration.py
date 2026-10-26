"""v2-enforcement-leaks Group A: example-based integration tests over the real ASGI app.

Fix-checking INTEGRATION tests (spec ``v2-enforcement-leaks``, task 28.4). The 10,000-iteration
property coverage for Group A lives in tasks 28.1-28.3 (``test_v2_leaks_a_properties.py``,
``test_v2_leaks_a_failclosed_properties.py``, ``test_v2_leaks_a_embeddings_properties.py``). This
file is the complementary EXAMPLE-based layer: a handful of concrete requests driven end-to-end
through an in-process ASGI app, asserting both sides of the model boundary (what the PROVIDER
receives and what the CLIENT receives) and the resource ledger.

Two in-process ASGI drivers are used, both of which are "the real app":

* The DEV PROFILE app (``serve.build_boot_dependencies`` + ``serve._assemble_app``), with a
  recording provider substituted for the echo, drives every case except the forced
  verification failure. The dev plan (``dev/plan.example.json``: pii -> redact, secret/credential
  -> block, every rule ``scope: both`` on chat and embeddings) is what produces the REDACT and
  BLOCK decisions. The provider records the exact forwarded ``BODY_DELTAS_KEY`` deltas AND is the
  echo source the output guard re-scans for the client-side assertion.
* The ``edge.app.build_app`` app over a REAL ``admit.lease.BudgetLease`` on ``fakeredis`` with a
  forcing ``input_detector`` drives the ONE forced-verification-failure embeddings case, because
  the dev echo provider cannot be made to emit an *unverifiable* REDACT decision through the dev
  plan alone (the dev plan only produces verifiable PII redactions). The forcing detector reports
  a REDACT finding whose span the resolver cannot cover (the task 28.2 ``_forcing_detector``
  out-of-range approach), which fails closed to ``input_unmaskable`` after admission and spend, so
  the refund is observable on the real lease. Both drivers are in-process ASGI, which is what the
  task asks for.

Cases (task 28.4):

Dev-profile chat, streaming AND non-streaming (``stream`` in {False, True}):
  * an SSN masked at the PROVIDER (``[REDACTED]``, no raw SSN in any forwarded delta) AND at the
    CLIENT (no raw SSN in the rendered body; ``[REDACTED]`` present);
  * an AWS key -> ``input_blocked`` with ZERO provider calls.

Dev-profile embeddings:
  * a LIST with an SSN in one item: that item masked, the other items byte-identical;
  * a STRING with an email: exactly one masked delta;
  * an AWS key in any item -> ``input_blocked`` with ZERO provider calls.

``build_app`` + forcing detector embeddings:
  * a forced verification failure -> HTTP 400 ``input_unmaskable``, ZERO provider calls, lease
    REFUNDED.

These assert the generalised behaviour of
``test_dev_profile_pipeline.py::test_client_never_sees_raw_ssn_and_aws_key_is_blocked`` (SSN masked
at the client + AWS key blocked) AND additionally assert the SSN is masked AT THE PROVIDER in the
same test, per the task.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import fakeredis
import pytest

from gateway_v2.admit.gcra import GcraParams, LocalGCRA
from gateway_v2.admit.grant import Admitted, ShedVerdict
from gateway_v2.admit.lease import BudgetLease, LeaseConfig
from gateway_v2.admit.quota import QuotaComponent, derive_low_watermark
from gateway_v2.detect.holdback import scan as holdback_scan
from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.transform import BODY_DELTAS_KEY
from gateway_v2.domain import Finding, Span
from gateway_v2.domain.category import Category
from gateway_v2.domain.finding import FindingStatus
from gateway_v2.edge import serve
from gateway_v2.edge.app import ASGIApplication, build_app
from gateway_v2.edge.cancel import InFlightSnapshot
from gateway_v2.edge.chat_deps import BudgetDecider
from gateway_v2.edge.input_enforcement import REFUSE_INPUT_UNMASKABLE
from gateway_v2.egress.output_guard import MinimalOutputResolver
from gateway_v2.egress.stream import ScanProtocol
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals
from gateway_v2.runtime.store_keys import KEYS
from tests.edge._chat_lifecycle_fixtures import (
    DEFAULT_ORG_ID,
    ResolvingIdentity,
    warm_snapshot_source,
)

# --------------------------------------------------------------------------- #
# Shared fixtures (dev plan, key, values, contract).
# --------------------------------------------------------------------------- #

EXAMPLE_PLAN = Path(__file__).resolve().parents[2] / "dev" / "plan.example.json"
SSN = "123-45-6789"
EMAIL = "jane@example.com"
#: The secret/credential class the dev plan BLOCKs (GW09 ``credential`` taxonomy).
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
KEY = "sk-dev-pipeline"
REDACTED = "[REDACTED]"
CHANNEL = "embeddings_input"

Delta = tuple[str, str]


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


# --------------------------------------------------------------------------- #
# The dev-profile recording provider + ASGI driver.
# --------------------------------------------------------------------------- #


class _RecordingProvider:
    """Records the exact forwarded ``(channel, text)`` deltas of every body, then echoes them.

    The echoed reply carries the forwarded text back so the output guard re-scans it, which is
    what lets the client-side assertion observe masking independently of the provider side. For
    embeddings each forwarded delta is answered with one scripted JSON vector, so
    ``EmbeddingsSurface._drain_vectors`` completes with HTTP 200.
    """

    def __init__(self) -> None:
        self.received: list[tuple[Delta, ...]] = []

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        return self._stream(request)

    async def _stream(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        raw = request.body.get(BODY_DELTAS_KEY, ())
        assert isinstance(raw, tuple)
        deltas = tuple((str(item[0]), str(item[1])) for item in raw)
        self.received.append(deltas)
        if deltas and deltas[0][0] == CHANNEL:
            for index, _delta in enumerate(deltas):
                vector = json.dumps([float(index), 0.5])
                yield UpstreamEvent(
                    text_deltas=((CHANNEL, vector),), final=index == len(deltas) - 1
                )
            return
        text = "\n".join(t for _c, t in deltas)
        yield UpstreamEvent(text_deltas=(("content", "echo: " + text),), final=True)

    async def abort(self) -> None:
        return None


def _dev_app(provider: _RecordingProvider) -> object:
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


async def _post(app: object, path: str, request_body: Mapping[str, object]) -> tuple[int, bytes]:
    body = json.dumps(request_body).encode()
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [(b"authorization", f"Bearer {KEY}".encode()), (b"content-type", b"json")],
    }
    sent: list[Mapping[str, object]] = []
    delivered = False

    async def _receive() -> Mapping[str, object]:
        nonlocal delivered
        if delivered:
            await asyncio.sleep(3600)
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def _send(message: Mapping[str, object]) -> None:
        sent.append(message)

    assert callable(app)
    await app(scope, _receive, _send)
    status = next(int(str(m["status"])) for m in sent if m["type"] == "http.response.start")
    payload = b"".join(
        bytes(m.get("body", b""))  # type: ignore[arg-type]
        for m in sent
        if m["type"] == "http.response.body"
    )
    return status, payload


def _post_chat(app: object, content: str, *, stream: bool) -> tuple[int, bytes]:
    request_body = {
        "model": "echo-1",
        "stream": stream,
        "messages": [{"role": "user", "content": content}],
    }
    return asyncio.run(_post(app, "/v1/chat/completions", request_body))


def _post_embeddings(app: object, embeddings_input: str | list[str]) -> tuple[int, bytes]:
    return asyncio.run(_post(app, "/v1/embeddings", {"model": "echo-1", "input": embeddings_input}))


# --------------------------------------------------------------------------- #
# Dev-profile chat: SSN masked at the provider AND the client; AWS key blocked.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("stream", [False, True])
def test_chat_ssn_masked_at_provider_and_client(stream: bool) -> None:
    """An SSN is ``[REDACTED]`` in the forwarded provider body AND absent from the client body.

    **Validates: Requirements 2.1, 2.3**
    """
    provider = _RecordingProvider()
    app = _dev_app(provider)
    status, payload = _post_chat(app, f"My SSN is {SSN} thanks", stream=stream)

    assert status == 200, f"stream={stream}: an input REDACT (no BLOCK) must proceed, got {status}"

    # Provider side: the one forwarded body carries [REDACTED], never the raw SSN.
    assert len(provider.received) == 1, f"stream={stream}: exactly one provider call"
    deltas = provider.received[0]
    for channel, text in deltas:
        assert SSN not in text, f"stream={stream}: raw SSN at the provider in {(channel, text)!r}"
    joined = "".join(text for _c, text in deltas)
    assert REDACTED in joined, f"stream={stream}: provider body missing {REDACTED}: {deltas!r}"

    # Client side: the rendered response body carries no raw SSN, and carries the masked echo.
    assert SSN.encode() not in payload, f"stream={stream}: raw SSN reached the client"
    assert REDACTED.encode() in payload, f"stream={stream}: client body missing {REDACTED}"


@pytest.mark.parametrize("stream", [False, True])
def test_chat_aws_key_is_input_blocked_with_zero_provider_calls(stream: bool) -> None:
    """An AWS key resolves to BLOCK: ``input_blocked`` body and ZERO provider calls.

    **Validates: Requirements 3.1**
    """
    provider = _RecordingProvider()
    app = _dev_app(provider)
    status, payload = _post_chat(app, f"key {AWS_KEY}", stream=stream)

    assert b"input_blocked" in payload, f"stream={stream}: expected input_blocked body: {payload!r}"
    assert len(provider.received) == 0, (
        f"stream={stream}: a BLOCKed input must make zero provider calls, "
        f"got {len(provider.received)}"
    )
    assert AWS_KEY.encode() not in payload, f"stream={stream}: raw key echoed to the client"
    del status


# --------------------------------------------------------------------------- #
# Dev-profile embeddings: per-item mask, shape preserved; AWS key blocked.
# --------------------------------------------------------------------------- #


def test_embeddings_list_masks_only_the_item_with_the_ssn() -> None:
    """A list input: the SSN item becomes ``[REDACTED]``; the other items are byte-identical.

    **Validates: Requirements 2.19, 2.20**
    """
    items = ["release notes", f"My SSN is {SSN}", "quarterly summary"]
    expected = (
        (CHANNEL, "release notes"),
        (CHANNEL, f"My SSN is {REDACTED}"),
        (CHANNEL, "quarterly summary"),
    )
    provider = _RecordingProvider()
    status, _payload = _post_embeddings(_dev_app(provider), items)

    assert status == 200, f"an input REDACT (no BLOCK) must proceed, got {status}"
    assert len(provider.received) == 1, "exactly one provider call"
    deltas = provider.received[0]
    for channel, text in deltas:
        assert SSN not in text, f"raw SSN at the provider in {(channel, text)!r}"
    assert deltas == expected, (
        f"the SSN item must be masked and the clean items byte-identical: "
        f"got {deltas!r}, expected {expected!r}"
    )


def test_embeddings_string_with_email_gives_one_masked_delta() -> None:
    """A string input with an email: exactly one delta, the email masked to ``[REDACTED]``.

    **Validates: Requirements 2.19, 2.20**
    """
    provider = _RecordingProvider()
    status, _payload = _post_embeddings(_dev_app(provider), f"mail {EMAIL}")

    assert status == 200, f"an input REDACT (no BLOCK) must proceed, got {status}"
    assert len(provider.received) == 1, "exactly one provider call"
    deltas = provider.received[0]
    assert deltas == ((CHANNEL, f"mail {REDACTED}"),), (
        f"a string input must give exactly one masked delta: {deltas!r}"
    )
    assert EMAIL not in deltas[0][1], f"raw email at the provider in {deltas[0]!r}"


def test_embeddings_aws_key_in_an_item_is_input_blocked_with_zero_provider_calls() -> None:
    """An AWS key in any list item resolves to BLOCK: ``input_blocked`` and ZERO provider calls.

    **Validates: Requirements 3.16**
    """
    provider = _RecordingProvider()
    status, payload = _post_embeddings(
        _dev_app(provider), ["release notes", f"key {AWS_KEY}", "summary"]
    )

    assert b"input_blocked" in payload, f"expected input_blocked body: {payload!r}"
    assert len(provider.received) == 0, (
        f"a BLOCKed embeddings input must make zero provider calls, got {len(provider.received)}"
    )
    assert AWS_KEY.encode() not in payload, "raw key echoed to the client"
    del status


# --------------------------------------------------------------------------- #
# The forced verification failure (build_app + forcing detector + real lease).
#
# The dev echo provider cannot be driven to an *unverifiable* REDACT through the dev plan alone,
# so this ONE case uses edge.app.build_app over a REAL BudgetLease on fakeredis with a forcing
# input_detector that reports an out-of-range REDACT finding (the task 28.2 pattern). That fails
# closed to input_unmaskable AFTER admission and spend, so the refund is observable on the lease.
# --------------------------------------------------------------------------- #

_PLAN_VERSION = "v2-leaks-28.4"
_ORG = DEFAULT_ORG_ID
_POOL = 1_000_000
_BIG_CHUNK = 100_000
#: A marker the forcing detector fires on; present in the forced request body.
_PROBE = "forceunmaskable"
#: An out-of-range span (far beyond any content), so the resolver's REDACT over it is unverifiable.
_FAR_START = 1_000_000
_FAR_END = _FAR_START + 11


def _forcing_detector(text: str) -> Sequence[Finding]:
    if _PROBE not in text:
        return ()
    return (
        Finding(
            detector="det",
            detector_version=_PLAN_VERSION,
            category=Category.PII,
            status=FindingStatus.EXECUTED,
            confidence=1.0,
            spans=(Span(start=_FAR_START, end=_FAR_END),),
            evidence=None,
        ),
    )


def _clean_detector(_text: str) -> Sequence[Finding]:
    return ()


class _CountingProvider:
    """Counts opens; echoes a fixed completion. A REDACT that fails closed never opens it."""

    __slots__ = ("opened",)

    def __init__(self) -> None:
        self.opened = 0

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        self.opened += 1
        return self._stream(request)

    async def _stream(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        del request
        yield UpstreamEvent(text_deltas=(("content", "hello there"),), final=True)

    async def abort(self) -> None:
        return None


class _Admission:
    """Admits every request; ``complete`` is a no-op (Group D owns its own tests)."""

    __slots__ = ("admitted",)

    def __init__(self) -> None:
        self.admitted: list[str] = []

    async def admit(
        self, owner_id: str, request_id: str, enqueued_at: float
    ) -> Admitted | ShedVerdict:
        self.admitted.append(request_id)
        return Admitted(owner_id=owner_id, request_id=request_id, admitted_at=enqueued_at)

    def complete(self, request_id: str) -> None:
        del request_id


class _Metrics:
    __slots__ = ()

    def exposition(self) -> str:
        return "# v2-leaks-28.4\n"


class _Control:
    __slots__ = ()

    def snapshot(self) -> InFlightSnapshot:
        return InFlightSnapshot(
            available=True,
            age_s=0.0,
            kill_switch_engaged=False,
            key_revoked=False,
            plan_changed=False,
        )


class _QuotaMetrics:
    __slots__ = ("budget_unavailable", "refills")

    def __init__(self) -> None:
        self.refills = 0
        self.budget_unavailable = 0

    def observe_async_refill(self) -> None:
        self.refills += 1

    def observe_budget_unavailable(self) -> None:
        self.budget_unavailable += 1


def _drop(work: Awaitable[None]) -> None:
    close = getattr(work, "close", None)
    if callable(close):
        close()


async def _real_quota(client: Any) -> tuple[QuotaComponent, BudgetLease]:
    await client.set(KEYS.budget_remaining(_ORG), _POOL)
    await client.set(KEYS.budget_generation(_ORG), 0)
    metrics = _QuotaMetrics()
    lease = BudgetLease(
        client,
        LeaseConfig(
            org=_ORG,
            chunk=_BIG_CHUNK,
            low_watermark=derive_low_watermark(_BIG_CHUNK),
            ttl_s=30.0,
        ),
        worker_id="w0",
        clock=lambda: 0.0,
        spawn=_drop,
        metrics=metrics,
    )
    assert await lease.acquire(0), "harness: the first chunk was not granted"
    gcra = LocalGCRA(GcraParams(rate_per_s=1_000_000.0, burst=1_000_000), clock=lambda: 0.0)
    quota = QuotaComponent(gcra, lease, generation_source=lambda: 0, metrics=metrics)
    return quota, lease


def _build_app(*, quota: BudgetDecider, provider: _CountingProvider) -> ASGIApplication:
    return build_app(
        contract=_contract(),
        provider=provider,
        scanner=cast(ScanProtocol, holdback_scan),
        resolver=MinimalOutputResolver(),
        metrics_registry=_Metrics(),
        clock=lambda: 0.0,
        control=_Control(),
        error_scanner=_clean_detector,
        input_detector=_forcing_detector,
        identity=ResolvingIdentity(),
        snapshot_source=warm_snapshot_source(),
        admission=_Admission(),
        quota=quota,
    )


async def _serve(app: ASGIApplication, body: bytes) -> tuple[int, dict[str, Any]]:
    sent: list[Mapping[str, object]] = []
    incoming: list[Mapping[str, object]] = [
        {"type": "http.request", "body": body, "more_body": False}
    ]
    scope: dict[str, object] = {
        "type": "http",
        "method": "POST",
        "path": "/v1/embeddings",
        "headers": [(b"content-type", b"application/json")],
    }

    async def _receive() -> Mapping[str, object]:
        if incoming:
            return incoming.pop(0)
        return {"type": "http.disconnect"}

    async def _send(message: Mapping[str, object]) -> None:
        sent.append(dict(message))

    await app(scope, _receive, _send)
    status = next(int(str(m["status"])) for m in sent if m["type"] == "http.response.start")
    raw = b"".join(
        bytes(chunk)
        for m in sent
        if m["type"] == "http.response.body"
        and isinstance(chunk := m.get("body", b""), (bytes, bytearray))
    )
    decoded = json.loads(raw) if raw else {}
    error = decoded.get("error") if isinstance(decoded, dict) else None
    return status, error if isinstance(error, dict) else {}


def test_embeddings_forced_verification_failure_refuses_400_zero_calls_and_refunds() -> None:
    """A forced unverifiable REDACT fails closed: HTTP 400, 0 provider opens, lease refunded.

    Driven through ``edge.app.build_app`` over a real ``BudgetLease`` on ``fakeredis`` (not the dev
    echo app) because the dev plan cannot produce an *unverifiable* REDACT. The forcing detector
    reports an out-of-range REDACT finding, so the staged body fails verification after admission
    and spend; the refund then runs via ``RequestHolds.release`` in the handler's ``finally`` and
    is observable as the lease ``remaining`` returning to its pre-spend value.

    **Validates: Requirements 2.2, 2.21**
    """
    body = json.dumps(
        {"model": "echo-1", "input": [f"please handle {_PROBE} now"]}
    ).encode("utf-8")

    async def _run() -> tuple[int, dict[str, Any], int, int, int]:
        client = fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer(), decode_responses=True)
        quota, lease = await _real_quota(client)
        held_before = lease.remaining
        provider = _CountingProvider()
        app = _build_app(quota=quota, provider=provider)
        status, error = await _serve(app, body)
        held_after = lease.remaining
        await client.aclose()
        return status, error, held_before, held_after, provider.opened

    status, error, before, after, opened = asyncio.run(_run())

    assert status == 400, f"expected HTTP 400, got {status} error {error!r}"
    assert error.get("type") == "invalid_request_error", (
        f"error.type {error.get('type')!r}, expected invalid_request_error"
    )
    assert error.get("code") == REFUSE_INPUT_UNMASKABLE, (
        f"error.code {error.get('code')!r}, expected {REFUSE_INPUT_UNMASKABLE}"
    )
    assert opened == 0, f"provider opened {opened} time(s), expected 0"
    assert after == before, f"spend not refunded -- lease held {before} before, {after} after"
