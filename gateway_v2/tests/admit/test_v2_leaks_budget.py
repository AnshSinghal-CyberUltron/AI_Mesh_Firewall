"""v2-enforcement-leaks Group F: the budget cost unit, the refund and the fair-share grant.

Bug-condition exploration test (spec ``v2-enforcement-leaks``, task 9; design Property 1 (F),
Property 12 and Property 16). Bug condition ``C_F``: the request reaches the budget phase; or
budget was spent with zero provider calls; or a lease acquire happens; or the output-budget fields
are off-contract. The expected behaviour (2.15-2.17):

* a request's cost and the lease are in ONE unit, tokens. A 2,000-character prompt is not refused
  ``budget_unavailable`` merely because the request-count Lease_Chunk is smaller than its cost;
* the charged cost is v1's ``estimate_prompt_tokens`` over v1's clamped request (``n`` forced to
  1, ``max_tokens`` capped at the org ceiling, 4096 by default). The frozen oracle is
  ``tests/admit/_v1_token_estimate_oracle.py``;
* ``max_tokens`` above ``1_000_000`` is refused HTTP 400 ``invalid_max_tokens`` with ``param``
  ``max_tokens``, before admission and with zero provider calls;
* a request that spent budget and is then refused with zero provider calls (input BLOCK) gets
  the spend refunded, on chat and on embeddings;
* a grant is at most ``pool // active_workers``.

**Seams (each exists on the unfixed tree and survives the fix).** Every case asserts an outcome,
never the existence of a new attribute:

* the lifecycle cases drive the default boot path (:func:`gateway_v2.edge.app.build_app`), not the
  dev profile (whose budget double charges one unit per request and would hide the bug);
* the cost case records the ``cost`` argument a budget double receives through the shipped
  ``BudgetDecider.evaluate(cost, request_id=...)`` seam;
* the refund and admission cases run the REAL :class:`QuotaComponent` over a REAL
  :class:`BudgetLease` on ``fakeredis`` and read the lease ledger (``lease.remaining``);
* the fair-share case reads each worker's grant as its ``lease.remaining`` delta across
  ``acquire`` and the pool from the store.

The lease used by the lifecycle cases is constructed the way the design's edge wiring will
construct it: ``overdraft_tokens=contract.max_request_tokens()`` is passed only when the fixed
``BudgetLease`` accepts it. On the unfixed tree that keyword does not exist, so the shipped lease
is used unchanged (:func:`_edge_lease_kwargs`).

Failing cases are committed under ``strict`` xfail (the spec's exploratory convention); each
has an unmarked control that holds today, so the xfail can only fail on the Group F outcome.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from typing import Any, cast

import fakeredis
import pytest

from gateway_v2.admit.gcra import GcraParams, LocalGCRA
from gateway_v2.admit.grant import Admitted, BudgetVerdict, ShedVerdict
from gateway_v2.admit.lease import BudgetLease, LeaseConfig
from gateway_v2.admit.quota import QuotaComponent, derive_lease_chunk, derive_low_watermark
from gateway_v2.detect.holdback import scan as holdback_scan
from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest
from gateway_v2.domain.category import Category
from gateway_v2.domain.finding import Finding, FindingStatus, Span
from gateway_v2.domain.posture import BUDGET_UNAVAILABLE
from gateway_v2.edge.app import ASGIApplication, build_app
from gateway_v2.edge.cancel import InFlightSnapshot
from gateway_v2.edge.chat_deps import BudgetDecider
from gateway_v2.edge.openai_chat import REFUSE_INPUT_BLOCKED
from gateway_v2.egress.output_guard import MinimalOutputResolver
from gateway_v2.egress.stream import ScanProtocol
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals
from gateway_v2.runtime.store_keys import KEYS
from tests.admit._v1_token_estimate_oracle import (
    DEFAULT_MAX_RESPONSE_TOKENS,
    MAX_OUTPUT_TOKENS_CEILING,
    V1InvalidMaxTokens,
    estimate_prompt_tokens,
    v1_clamp,
    v1_estimate,
)
from tests.edge._chat_lifecycle_fixtures import (
    DEFAULT_ORG_ID,
    ResolvingIdentity,
    warm_snapshot_source,
)

_ORG = DEFAULT_ORG_ID
_CHAT = "/v1/chat/completions"
_EMBEDDINGS = "/v1/embeddings"
_MODEL = "gpt-4o-mini"

#: The measured guard rate the request-count chunk is derived from. Under :func:`_contract` this
#: gives ``derive_lease_chunk == 6`` (``ceil(1000 * 0.020) = 20``, bounded by ``queue_depth = 6``).
_Q_SAFE = 1000.0
#: An org pool far larger than any cost here, so only the chunk can refuse.
_POOL = 1_000_000
#: A lease chunk far larger than any cost here (the refund cases must be admitted by budget).
_BIG_CHUNK = 100_000
#: A 2,000-character plain-ASCII prompt (no ``{`` or ``,``, so the part-count pre-check is quiet).
_PROMPT_2000 = ("lorem ipsum dolor sit amet " * 80)[:2000]
#: An input value the input detector double flags ``card`` (BLOCK under MinimalOutputResolver).
_CARD = "4111111111111111"


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
# Doubles.
# --------------------------------------------------------------------------- #


class _Admission:
    """Admits every request and counts admits; ``complete`` is a no-op (Group D owns it)."""

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


class _RecordingBudget:
    """Admits every request and records the ``cost`` the lifecycle charges."""

    __slots__ = ("costs",)

    def __init__(self) -> None:
        self.costs: list[int] = []

    async def evaluate(self, cost: int, *, request_id: str) -> BudgetVerdict | Admitted:
        self.costs.append(cost)
        return Admitted(owner_id=_ORG, request_id=request_id, admitted_at=0.0)

    def refund(self, cost: int, *, request_id: str) -> None:
        del cost, request_id


class _Provider:
    """Echoes a fixed completion (chat) or one vector per item (embeddings); counts opens."""

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


class _Metrics:
    __slots__ = ()

    def exposition(self) -> str:
        return "# v2-leaks-f\n"


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
    """Off-path refill spawn that never runs the refill (keeps the ledger deterministic)."""
    close = getattr(work, "close", None)
    if callable(close):
        close()


def _input_detector(*, block: bool) -> Callable[[str], Sequence[Finding]]:
    def _scan(text: str) -> Sequence[Finding]:
        if not block or _CARD not in text:
            return ()
        start = text.index(_CARD)
        return (
            Finding(
                detector="card",
                detector_version="v2-leaks-f",
                category=Category.CREDENTIAL,
                status=FindingStatus.EXECUTED,
                confidence=1.0,
                spans=(Span(start=start, end=start + len(_CARD)),),
                evidence=None,
            ),
        )

    return _scan


def _edge_lease_kwargs(contract: ResourceContract) -> dict[str, Any]:
    """The design's edge lease wiring: ``overdraft_tokens=contract.max_request_tokens()``.

    Returns ``{}`` on the unfixed tree, where ``BudgetLease`` has no ``overdraft_tokens``.
    """
    if "overdraft_tokens" not in inspect.signature(BudgetLease).parameters:
        return {}
    bound: Callable[[], int] = getattr(contract, "max_request_tokens")
    return {"overdraft_tokens": bound()}


async def _real_quota(
    client: Any, contract: ResourceContract, *, chunk: int
) -> tuple[QuotaComponent, BudgetLease]:
    """A REAL QuotaComponent over a REAL BudgetLease on ``client``, first chunk acquired."""
    await client.set(KEYS.budget_remaining(_ORG), _POOL)
    await client.set(KEYS.budget_generation(_ORG), 0)
    metrics = _QuotaMetrics()
    lease = BudgetLease(
        client,
        LeaseConfig(org=_ORG, chunk=chunk, low_watermark=derive_low_watermark(chunk), ttl_s=30.0),
        worker_id="w0",
        clock=lambda: 0.0,
        spawn=_drop,
        metrics=metrics,
        **_edge_lease_kwargs(contract),
    )
    assert await lease.acquire(0), "harness: the first chunk was not granted"
    gcra = LocalGCRA(GcraParams(rate_per_s=1_000_000.0, burst=1_000_000), clock=lambda: 0.0)
    quota = QuotaComponent(gcra, lease, generation_source=lambda: 0, metrics=metrics)
    return quota, lease


def _app(
    *,
    quota: BudgetDecider,
    admission: _Admission,
    provider: _Provider,
    block: bool = False,
) -> ASGIApplication:
    return build_app(
        contract=_contract(),
        provider=provider,
        scanner=cast(ScanProtocol, holdback_scan),
        resolver=MinimalOutputResolver(),
        metrics_registry=_Metrics(),
        clock=lambda: 0.0,
        control=_Control(),
        error_scanner=_input_detector(block=False),
        input_detector=_input_detector(block=block),
        identity=ResolvingIdentity(),
        snapshot_source=warm_snapshot_source(),
        admission=admission,
        quota=quota,
    )


async def _serve(app: ASGIApplication, path: str, body: bytes) -> tuple[int, dict[str, Any]]:
    """One non-streaming request through the ASGI app; returns ``(status, error-object)``."""
    sent: list[Mapping[str, object]] = []
    incoming: list[Mapping[str, object]] = [
        {"type": "http.request", "body": body, "more_body": False},
    ]
    scope: dict[str, object] = {
        "type": "http",
        "method": "POST",
        "path": path,
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


def _chat(content: str, **fields: object) -> dict[str, object]:
    return {"model": _MODEL, "messages": [{"role": "user", "content": content}], **fields}


def _encode(obj: Mapping[str, object]) -> bytes:
    return json.dumps(obj).encode("utf-8")


# --------------------------------------------------------------------------- #
# The frozen v1 oracle itself (holds today; it is v1 behaviour, not v2).
# --------------------------------------------------------------------------- #


def test_v1_oracle_matches_the_v1_formula_and_clamp() -> None:
    """Sanity of the oracle: known v1 values for the estimate and the clamp."""
    assert estimate_prompt_tokens([]) == 1
    assert estimate_prompt_tokens([{"content": "abc"}]) == 1
    assert estimate_prompt_tokens([{"content": "x" * 2000}], 4096, 1) == 500 + 4096
    assert estimate_prompt_tokens([{"content": "x" * 2000}], 100, 3) == 500 + 300
    parts = [{"type": "text", "text": "a" * 12}, {"type": "text", "text": "b" * 12}]
    assert estimate_prompt_tokens([{"content": parts}]) == (12 + 1 + 12) // 4

    clamped = v1_clamp(_chat("hi", max_tokens=4096, n=8))
    assert clamped.body["n"] == 1
    assert clamped.body["max_tokens"] == DEFAULT_MAX_RESPONSE_TOKENS
    assert clamped.request_clamps == ("n=8->1",)

    big = v1_clamp(_chat("hi", max_tokens=MAX_OUTPUT_TOKENS_CEILING))
    assert big.body["max_tokens"] == DEFAULT_MAX_RESPONSE_TOKENS
    assert big.request_clamps == ("max_tokens=1000000->4096",)
    assert (
        v1_clamp(_chat("hi", max_tokens=1_000_000), max_response_tokens=100_000).body["max_tokens"]
        == 65536
    )

    assert "max_tokens" not in v1_clamp(_chat("hi", max_tokens=None)).body
    assert v1_clamp(_chat("hi", max_tokens=0)).body["max_tokens"] == 0
    with pytest.raises(V1InvalidMaxTokens) as refused:
        v1_clamp(_chat("hi", max_tokens=2_000_000))
    assert (refused.value.status, refused.value.code, refused.value.param) == (
        400,
        "invalid_max_tokens",
        "max_tokens",
    )
    assert v1_estimate(_chat("x" * 2000, max_tokens=4096, n=8)) == 500 + 4096


# --------------------------------------------------------------------------- #
# 1.15 / 2.16: a prompt whose cost exceeds the request-count chunk.
# --------------------------------------------------------------------------- #


def _serve_with_real_quota(chunk: int, body: bytes) -> tuple[int, dict[str, Any], int]:
    async def _run() -> tuple[int, dict[str, Any], int]:
        client = fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer(), decode_responses=True)
        quota, _lease = await _real_quota(client, _contract(), chunk=chunk)
        provider = _Provider()
        app = _app(quota=quota, admission=_Admission(), provider=provider)
        status, error = await _serve(app, _CHAT, body)
        await client.aclose()
        return status, error, provider.opened

    return asyncio.run(_run())


def test_large_chunk_admits_the_2000_char_prompt() -> None:
    """Control: with a chunk larger than any cost, the same prompt is served (holds today)."""
    status, error, opened = _serve_with_real_quota(_BIG_CHUNK, _encode(_chat(_PROMPT_2000)))
    assert (status, opened) == (200, 1), f"control: status {status} error {error!r}"


def test_prompt_above_the_request_count_chunk_is_admitted() -> None:
    """A cost above one chunk but within the org budget is not refused (2.16).

    **Validates: Requirements 1.15, 2.15, 2.16**
    """
    chunk = derive_lease_chunk(_contract(), q_safe=_Q_SAFE)
    assert chunk < len(_PROMPT_2000) // 4, f"harness: chunk {chunk} must be below the cost"
    status, error, opened = _serve_with_real_quota(chunk, _encode(_chat(_PROMPT_2000)))
    assert error.get("code") != BUDGET_UNAVAILABLE, (
        f"chunk={chunk}: 2000-char prompt refused {error!r} with the org pool at {_POOL}"
    )
    assert (status, opened) == (200, 1), f"chunk={chunk}: status {status} error {error!r}"


# --------------------------------------------------------------------------- #
# 2.15: the charged cost is v1's estimate over the clamped request.
# --------------------------------------------------------------------------- #


def test_budget_charges_the_v1_estimate_over_the_clamped_request() -> None:
    """The budget phase charges ``estimate_prompt_tokens`` after v1's clamp (Property 12).

    **Validates: Requirements 2.15**
    """
    request = _chat(_PROMPT_2000, max_tokens=4096, n=8)
    expected = v1_estimate(request)
    budget = _RecordingBudget()

    async def _run() -> int:
        app = _app(quota=budget, admission=_Admission(), provider=_Provider())
        status, _error = await _serve(app, _CHAT, _encode(request))
        return status

    status = asyncio.run(_run())
    assert len(budget.costs) == 1, f"harness: evaluate calls {budget.costs!r} (status {status})"
    assert budget.costs == [expected], (
        f"max_tokens=4096 n=8, 2000 chars: charged {budget.costs[0]}, v1 oracle {expected}"
    )


# --------------------------------------------------------------------------- #
# Property 16: max_tokens above 1_000_000 is refused at the boundary.
# --------------------------------------------------------------------------- #


def _serve_max_tokens(max_tokens: int) -> tuple[int, dict[str, Any], list[str], int, list[int]]:
    budget = _RecordingBudget()
    admission = _Admission()
    provider = _Provider()

    async def _run() -> tuple[int, dict[str, Any]]:
        app = _app(quota=budget, admission=admission, provider=provider)
        return await _serve(app, _CHAT, _encode(_chat("hello", max_tokens=max_tokens)))

    status, error = asyncio.run(_run())
    return status, error, admission.admitted, provider.opened, budget.costs


def test_max_tokens_at_the_boundary_is_accepted() -> None:
    """Control: ``max_tokens == 1_000_000`` is accepted (clamped later, never refused)."""
    status, error, admitted, opened, _costs = _serve_max_tokens(MAX_OUTPUT_TOKENS_CEILING)
    assert (status, len(admitted), opened) == (200, 1, 1), f"control: {status} {error!r}"


def test_max_tokens_above_the_boundary_is_refused_before_admission() -> None:
    """v1's boundary refusal (Property 16): 400 ``invalid_max_tokens``, nothing admitted.

    **Validates: Requirements 2.15, 2.16**
    """
    status, error, admitted, opened, costs = _serve_max_tokens(2_000_000)
    observed = (status, error.get("code"), error.get("param"), error.get("request_id"))
    assert observed == (400, "invalid_max_tokens", "max_tokens", None), (
        f"max_tokens=2000000: observed {observed!r}, admitted {admitted!r}, opened {opened}"
    )
    assert admitted == [] and costs == [] and opened == 0, (
        f"max_tokens=2000000: admitted {admitted!r}, charged {costs!r}, provider opened {opened}"
    )


# --------------------------------------------------------------------------- #
# 1.17 / 2.17: input BLOCK after a spend refunds the spend.
# --------------------------------------------------------------------------- #


def _blocked_spend(path: str) -> tuple[int, dict[str, Any], int, int, int]:
    """Serve a BLOCKed request on a real lease: status, error, held before/after, provider opens."""
    if path == _EMBEDDINGS:
        body = _encode({"model": "echo-1", "input": [f"pay with {_CARD} please"]})
    else:
        body = _encode(_chat(f"pay with {_CARD} please"))

    async def _run() -> tuple[int, dict[str, Any], int, int, int]:
        client = fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer(), decode_responses=True)
        quota, lease = await _real_quota(client, _contract(), chunk=_BIG_CHUNK)
        held_before = lease.remaining
        provider = _Provider()
        app = _app(quota=quota, admission=_Admission(), provider=provider, block=True)
        status, error = await _serve(app, path, body)
        held_after = lease.remaining
        await client.aclose()
        return status, error, held_before, held_after, provider.opened

    return asyncio.run(_run())


@pytest.mark.parametrize("path", [_CHAT, _EMBEDDINGS], ids=["chat", "embeddings"])
def test_input_block_is_reached_with_zero_provider_calls(path: str) -> None:
    """Control: the BLOCK exit is reached over the real lease with zero provider calls."""
    status, error, _before, _after, opened = _blocked_spend(path)
    assert (error.get("code"), opened) == (REFUSE_INPUT_BLOCKED, 0), f"{path}: {status} {error!r}"


@pytest.mark.parametrize("path", [_CHAT, _EMBEDDINGS], ids=["chat", "embeddings"])
def test_input_block_refunds_the_spend(path: str) -> None:
    """A spend followed by a zero-provider-call refusal is refunded to the lease (2.17).

    **Validates: Requirements 1.17, 2.17**
    """
    status, error, before, after, opened = _blocked_spend(path)
    assert (error.get("code"), opened) == (REFUSE_INPUT_BLOCKED, 0), f"{path}: {status} {error!r}"
    assert after == before, (
        f"{path}: input_blocked with 0 provider calls; lease held {before} before, {after} after"
    )


# --------------------------------------------------------------------------- #
# 1.16 / Property 12: the grant is at most pool // active_workers.
# --------------------------------------------------------------------------- #

#: The fair-share scenario: the per-worker cap stays far above ``pool // 4`` so only the fair
#: share can bound the fourth grant.
_SHARE_POOL = 10_000
_SHARE_CAP = 3_000


async def _grants(workers: int) -> list[tuple[int, int]]:
    """``workers`` leases acquire in turn over one pool; returns ``(pool_before, grant)`` each.

    Every earlier worker still holds its lease when the next acquires, so worker ``k`` (0-based)
    acquires with ``k + 1`` active workers.
    """
    client = fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer(), decode_responses=True)
    await client.set(KEYS.budget_remaining(_ORG), _SHARE_POOL)
    await client.set(KEYS.budget_generation(_ORG), 0)
    observed: list[tuple[int, int]] = []
    for index in range(workers):
        lease = BudgetLease(
            client,
            LeaseConfig(org=_ORG, chunk=_SHARE_CAP, low_watermark=0, ttl_s=30.0),
            worker_id=f"w{index}",
            clock=lambda: 0.0,
            spawn=_drop,
            metrics=_QuotaMetrics(),
        )
        pool_before = int(await client.get(KEYS.budget_remaining(_ORG)) or 0)
        held_before = lease.remaining
        await lease.acquire(0)
        observed.append((pool_before, lease.remaining - held_before))
    await client.aclose()
    return observed


def test_single_worker_grant_is_bounded_by_pool_and_cap() -> None:
    """Control: one active worker is granted ``min(pool, cap)`` (holds today and after)."""
    ((pool_before, grant),) = asyncio.run(_grants(1))
    assert grant == min(pool_before, _SHARE_CAP), f"control: pool {pool_before} grant {grant}"


def test_grant_with_four_active_workers_is_at_most_a_quarter_of_the_pool() -> None:
    """With 4 active workers a grant is at most ``pool // 4`` (C29, Property 12).

    **Validates: Requirements 1.16, 2.15**
    """
    observed = asyncio.run(_grants(4))
    pool_before, grant = observed[3]
    assert grant <= max(1, pool_before // 4), (
        f"4 active workers, pool {pool_before}: grant {grant} > {pool_before // 4}; "
        f"all (pool_before, grant) = {observed!r}"
    )
