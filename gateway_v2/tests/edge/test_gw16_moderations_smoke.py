"""GW16 task 5.3 focused smoke test — ``ModerationsSurface.handle`` lifecycle.

NOT the property suite. This is the task's own in-process smoke check that
``ModerationsSurface(deps=MiscRoute).handle`` drives the SCAN-ONLY lifecycle
(identity → input scan → render findings-as-categories) correctly across five
scenarios, and — the hard gate — makes ZERO provider calls on every one of them
(moderations opens NO provider, R8.5), verified with a counted
``StubProviderClient``:

(a) ``{}`` body (missing ``input``) ⇒ HTTP 400 ``missing_required_parameter``.
(b) fail-closed identity ⇒ HTTP 503 ``shared_state_unavailable``.
(c) benign input + a no-finding detector ⇒ HTTP 200 moderation body, ``flagged:false``.
(d) an input + a finding-producing detector ⇒ HTTP 200 body with the matching
    category flagged + overall ``flagged:true``.
(e) a RAISING detector ⇒ ``scan_failure`` withhold, NO moderation result body
    (never a ``flagged:false`` unscanned result — R8.6).

Every scenario asserts ``open_calls == 0`` and ``fail_open_total == 0``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence

from gateway_v2.dispatch.provider import StubProviderClient, UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.routing import DispatchRouter
from gateway_v2.dispatch.transform import Transform
from gateway_v2.domain.category import Category
from gateway_v2.domain.finding import Finding, FindingStatus, Span
from gateway_v2.domain.identity import Principal
from gateway_v2.domain.plan import ExecutionPlan, StreamingMode
from gateway_v2.edge.app import empty_catalog_source
from gateway_v2.edge.chat_deps import InputDetector
from gateway_v2.edge.identity_resolver import (
    IdentityResolver,
    TenantIdentity,
    fail_closed_resolver,
)
from gateway_v2.edge.openai_misc import MiscRoute, ModerationsSurface
from gateway_v2.edge.wire.openai_misc import MiscResponseRenderer
from gateway_v2.egress.output_guard import MinimalOutputResolver
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

# --------------------------------------------------------------------------- #
# Test doubles (real StubProviderClient; minimal fail-closed-or-permissive deps).
# --------------------------------------------------------------------------- #


class _CountingProvider(StubProviderClient):
    """A real :class:`StubProviderClient` (no socket) that counts every ``open``.

    Moderations must NEVER open the provider (R8.5): every scenario asserts
    ``open_calls == 0``.
    """

    __slots__ = ("open_calls",)

    def __init__(self) -> None:
        import random

        super().__init__(script=(), rng=random.Random(0))
        self.open_calls = 0

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        self.open_calls += 1
        return super().open(request)


class _FixedIdentity:
    """An ``IdentityResolver`` that resolves one canned tenant."""

    __slots__ = ("_identity",)

    def __init__(self, identity: TenantIdentity) -> None:
        self._identity = identity

    async def resolve(self, scope: Mapping[str, object]) -> TenantIdentity | None:
        del scope
        return self._identity


class _FindingDetector:
    """A detector that emits one EXECUTED finding of ``category`` when the canary is present."""

    __slots__ = ("_canary", "_category")

    def __init__(self, canary: str, category: Category) -> None:
        self._canary = canary
        self._category = category

    def __call__(self, text: str) -> Sequence[Finding]:
        if self._canary not in text:
            return ()
        start = text.index(self._canary)
        return (
            Finding(
                detector="gw16-moderation",
                detector_version="gw16",
                category=self._category,
                status=FindingStatus.EXECUTED,
                confidence=0.9,
                spans=(Span(start=start, end=start + len(self._canary)),),
                evidence=None,
            ),
        )


class _RaisingDetector:
    """A detector whose scan cannot be computed — it RAISES (uncomputable scan, R8.6)."""

    __slots__ = ()

    def __call__(self, text: str) -> Sequence[Finding]:
        del text
        raise RuntimeError("detector blew up")


def _no_findings(text: str) -> Sequence[Finding]:
    del text
    return ()


def _plan() -> ExecutionPlan:
    return ExecutionPlan(
        org_id="gw16-org",
        epoch=1,
        sequence=1,
        content_hash="gw16" * 8,
        compiled_at=0.0,
        rules=(),
        required_detectors=frozenset(),
        streaming_mode=StreamingMode.INCREMENTAL,
        integrity_locked=False,
    )


def _identity() -> TenantIdentity:
    return TenantIdentity(
        org_id="gw16-org",
        principal=Principal(
            key_id="gw16-key",
            org_id="gw16-org",
            rate_per_s=1.0,
            burst=1.0,
            feed_seq=0,
        ),
    )


# --------------------------------------------------------------------------- #
# A tiny ASGI receive/send harness.
# --------------------------------------------------------------------------- #


def _receive_for(body: bytes) -> Callable[[], Awaitable[Mapping[str, object]]]:
    """An ASGI ``receive`` that delivers ``body`` as one terminal http.request event."""
    delivered = False

    async def receive() -> Mapping[str, object]:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


class _Capture:
    """An ASGI ``send`` that captures the response start + body events."""

    def __init__(self) -> None:
        self.status: int | None = None
        self.body: bytes = b""

    async def __call__(self, event: Mapping[str, object]) -> None:
        if event.get("type") == "http.response.start":
            self.status = int(event["status"])  # type: ignore[call-overload]
        elif event.get("type") == "http.response.body":
            chunk = event.get("body")
            if isinstance(chunk, (bytes, bytearray)):
                self.body += bytes(chunk)


_SCOPE: Mapping[str, object] = {"type": "http", "method": "POST", "path": "/v1/moderations"}


def _contract() -> ResourceContract:
    """A serviceable contract (the shared ``from_signals`` fixture shape)."""
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=400 * 1024 * 1024 * 8,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals,
        target_p99_ms=20.0,
        utilization_cap=0.75,
        per_worker_rss=400 * 1024 * 1024,
    )


def _deps(
    provider: _CountingProvider,
    *,
    identity: IdentityResolver,
    detector: InputDetector,
) -> MiscRoute:
    return MiscRoute(
        contract=_contract(),
        provider=provider,
        clock=lambda: 0.0,
        renderer=MiscResponseRenderer(),
        catalog=empty_catalog_source,  # unused by moderations; the honest-empty default.
        identity=identity,
        input_detector=detector,
        resolver=MinimalOutputResolver(),
        router=DispatchRouter(),
        transform=Transform(),
    )


def _run(deps: MiscRoute, body: bytes) -> _Capture:
    capture = _Capture()
    asyncio.run(ModerationsSurface(deps=deps).handle(_SCOPE, _receive_for(body), capture))
    return capture


# --------------------------------------------------------------------------- #
# (a) missing input ⇒ 400 missing_required_parameter, zero provider calls.
# --------------------------------------------------------------------------- #


def test_moderations_missing_input_renders_400_missing_required_parameter() -> None:
    provider = _CountingProvider()
    deps = _deps(provider, identity=_FixedIdentity(_identity()), detector=_no_findings)

    capture = _run(deps, b"{}")

    assert capture.status == 400
    error = json.loads(capture.body)["error"]
    assert error["type"] == "invalid_request_error"
    assert error["code"] == "missing_required_parameter"
    assert provider.open_calls == 0
    assert deps.exports.snapshot().fail_open_total == 0


# --------------------------------------------------------------------------- #
# (b) fail-closed identity ⇒ 503 shared_state_unavailable, zero provider calls.
# --------------------------------------------------------------------------- #


def test_moderations_fail_closed_identity_refuses_with_zero_provider_calls() -> None:
    provider = _CountingProvider()
    deps = _deps(provider, identity=fail_closed_resolver, detector=_no_findings)
    body = json.dumps({"input": "hello"}).encode()

    capture = _run(deps, body)

    assert capture.status == 503
    assert provider.open_calls == 0
    assert deps.exports.snapshot().fail_open_total == 0


# --------------------------------------------------------------------------- #
# (c) benign input + no-finding detector ⇒ 200 body, flagged:false, zero provider calls.
# --------------------------------------------------------------------------- #


def test_moderations_benign_input_renders_unflagged_result() -> None:
    provider = _CountingProvider()
    deps = _deps(provider, identity=_FixedIdentity(_identity()), detector=_no_findings)
    body = json.dumps({"input": ["clean one", "clean two"]}).encode()

    capture = _run(deps, body)

    assert capture.status == 200
    assert provider.open_calls == 0  # moderations opens NO provider (R8.5).
    rendered = json.loads(capture.body)
    assert rendered["id"].startswith("modr-")
    assert rendered["model"] == "text-moderation-latest"  # default echoed (model omitted).
    result = rendered["results"][0]
    assert result["flagged"] is False
    assert result["categories"] == {}
    assert deps.exports.snapshot().fail_open_total == 0


# --------------------------------------------------------------------------- #
# (d) a finding-producing detector ⇒ 200 body, matching category flagged, flagged:true.
# --------------------------------------------------------------------------- #


def test_moderations_finding_flags_matching_category() -> None:
    canary = "GW16PII"
    provider = _CountingProvider()
    detector = _FindingDetector(canary, Category.PII)
    deps = _deps(provider, identity=_FixedIdentity(_identity()), detector=detector)
    # model present this time ⇒ echoed back verbatim.
    body = json.dumps({"model": "my-mod", "input": ["clean", f"carry {canary} here"]}).encode()

    capture = _run(deps, body)

    assert capture.status == 200
    assert provider.open_calls == 0  # the hard gate — NO provider even with a finding (R8.5).
    rendered = json.loads(capture.body)
    assert rendered["model"] == "my-mod"
    result = rendered["results"][0]
    assert result["flagged"] is True
    # Category.PII maps to the "pii" moderation category (a firewall EXTRA), flagged true.
    assert result["categories"]["pii"] is True
    assert result["category_scores"]["pii"] == 0.9
    assert deps.exports.snapshot().fail_open_total == 0


# --------------------------------------------------------------------------- #
# (e) a RAISING detector ⇒ scan_failure withhold, NO result body, zero provider calls (R8.6).
# --------------------------------------------------------------------------- #


def test_moderations_uncomputable_scan_withholds_no_result_body() -> None:
    provider = _CountingProvider()
    deps = _deps(provider, identity=_FixedIdentity(_identity()), detector=_RaisingDetector())
    body = json.dumps({"input": "anything"}).encode()

    capture = _run(deps, body)

    # Fail CLOSED: the uncomputable scan is a withholding, NOT a benign flagged:false result.
    # ``scan_failure`` renders the declared withhold envelope (HTTP 403 ``gateway_error``,
    # code ``scan_failure``) with no raw internal detail — and crucially the body carries NO
    # moderation ``results`` (R8.6: never an unscanned flagged:false result body).
    assert provider.open_calls == 0
    assert capture.status == 403
    payload = json.loads(capture.body)
    assert "results" not in payload  # never an unscanned moderation result body (R8.6).
    assert payload["error"]["type"] == "gateway_error"
    assert payload["error"]["code"] == "scan_failure"
    assert deps.exports.snapshot().fail_open_total == 0
