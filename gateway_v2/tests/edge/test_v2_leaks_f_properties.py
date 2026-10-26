"""v2-enforcement-leaks Group F (surface half): the token-cost contract over seeded requests.

Fix-checking property test (spec ``v2-enforcement-leaks``, task 34.1; design Property 12, Bug
Condition F). Where task 23.6 proves the lease-ledger half of Property 12 (grant bound, overdraft,
refund-within-generation), this file proves the SURFACE half: what every billable surface and the
dev profile CHARGE the lease, and that a zero-dispatch refusal gives the charge back.

**What the 10,000 seeded ``(messages, max_tokens, n)`` draws assert.**

1. *The charge is the contract estimate over the CLAMPED output budget.* Every surface charges
   ``ResourceContract.estimate_request_tokens(prompt_chars(req), req.max_tokens, req.n)`` over the
   request AFTER ``clamp_output_budget`` (chat) or ``estimate_request_tokens(sum(len(item)), 0, 1)``
   (embeddings). The unit tier recomputes that value from the parsed+clamped request; the
   end-to-end tier reads the cost a recording :class:`BudgetDecider` double actually received
   through the shipped ``evaluate(cost, request_id=...)`` seam and asserts it equals the recompute.

2. *v1 parity on a message-content-only chat request; a lower bound otherwise.* When a chat
   request's ONLY folded input text is message content (string ``content`` or ``content_part_text``
   parts -- no ``tool_arguments`` / ``function_arguments``), the charge equals the FROZEN v1 oracle
   (``tests/admit/_v1_token_estimate_oracle``: v1's ``estimate_prompt_tokens`` applied AFTER v1's
   clamp). This holds because ``prompt_chars`` reproduces v1's folded-input length exactly -- it
   joins a message's content parts with one separator, as v1 does -- and ``clamp_output_budget``
   reproduces v1's ``n`` and ``max_tokens`` clamp. When a request also carries tool/function
   argument text, ``prompt_chars`` counts those extra channels that v1's message-only estimate
   does not, so the charge is >= the oracle (never below it).

3. *A cost larger than one lease chunk but within the org budget is ADMITTED.* The request-count
   ``derive_lease_chunk`` is a few requests wide; a long prompt costs far more tokens than that.
   Because the cost and the lease are now in ONE unit (tokens), such a request is served, not
   refused ``budget_unavailable`` -- the Group F unit-mismatch bug. Driven over a REAL
   :class:`BudgetLease` on ``fakeredis`` with the small request-count chunk and a large pool.

4. *Input BLOCK, scan failure and ``input_unmaskable`` REFUND the spend, on chat and embeddings.*
   Each refusal spends budget then opens no provider, so ``RequestHolds.release`` refunds the spend
   in the handler's ``finally``. Observed as the lease ``remaining`` restored to its pre-spend value
   over a real lease. ``input_unmaskable`` is forced by an injected input detector whose
   out-of-range REDACT finding cannot be verified against the outbound bytes (the Group A/D idiom).

**The dev profile.** The design's dev lease (``edge/dev_admit.DevLeaseBudget``) passes the SAME
``estimate_request_tokens`` cost the edge lifecycle computes straight through to the shipped
``QuotaComponent.evaluate`` (clause 5 of Property 12). Driving the dev ASGI here would require a
real Redis; instead the charge the dev profile would levy is exactly the lifecycle charge this test
pins through the default boot (the surfaces compute ``_estimated_cost`` identically on both boot
paths), and the dev-specific wiring is covered by ``tests/edge/test_dev_profile_components.py``
(task 27.4). This test asserts the surface charge, which the dev profile forwards unchanged.

**Drive-layer split.** Every iteration runs the UNIT tier (parse, clamp, recompute the charge, v1
oracle comparison). Every ``_E2E_EVERY``-th iteration ALSO runs an END-TO-END tier: it drives the
full §10.4 lifecycle through ``edge/app.build_app`` with a recording budget double and asserts the
charged cost equals the recompute, over chat (message-only and tool-bearing) and embeddings. The
admission (clause 3) and refund (clause 4) clauses run as focused example checks over a real lease,
because they need the real ``QuotaComponent`` / ``BudgetLease`` ledger, not a double.

No hypothesis; one ``random.Random(_SEED)`` seeds every draw; the seed is in every assertion
message. These are TEST fakes (``tests/`` is outside the import-linter layer and the AST/size
gates), so they import freely across layers.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator, Awaitable, Mapping, Sequence
from typing import Any, cast

import fakeredis

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
from gateway_v2.edge.openai_chat import (
    REFUSE_INPUT_BLOCKED,
    REFUSE_INPUT_SCAN_FAILURE,
    REFUSE_INPUT_UNMASKABLE,
)
from gateway_v2.edge.wire.openai import (
    OpenAIRequestParser,
    Surface,
    clamp_output_budget,
    prompt_chars,
)
from gateway_v2.egress.output_guard import MinimalOutputResolver
from gateway_v2.egress.stream import ScanProtocol
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals
from gateway_v2.runtime.store_keys import KEYS
from tests.admit._v1_token_estimate_oracle import (
    DEFAULT_MAX_RESPONSE_TOKENS,
    v1_estimate,
)
from tests.edge._chat_lifecycle_fixtures import (
    DEFAULT_ORG_ID,
    ResolvingIdentity,
    warm_snapshot_source,
)

# --------------------------------------------------------------------------- #
# Run shape.
# --------------------------------------------------------------------------- #

#: Seeded, deterministic; the seed is echoed in every assertion message.
_SEED = 0xF00D_B0DE_C105ED
#: At least 10,000 iterations (the spec's property idiom: seeded ``random.Random``, no hypothesis).
_ITERATIONS = 10_000
#: Every ``_E2E_EVERY``-th iteration ALSO drives the full ASGI lifecycle with a recording budget
#: and reads the cost actually charged. 10,000 // 200 = 50 end-to-end cases -- a non-trivial subset
#: covering chat (message-only + tool-bearing) and embeddings while keeping the run fast.
_E2E_EVERY = 200

_ORG = DEFAULT_ORG_ID
_CHAT = "/v1/chat/completions"
_EMBEDDINGS = "/v1/embeddings"
_MODEL = "gpt-4o-mini"

#: The org ceiling the chat surface clamps against (``default_response_token_ceiling()`` == v1's
#: 4096). The oracle is driven with the same ceiling (its default), so the two clamps agree.
assert DEFAULT_MAX_RESPONSE_TOKENS == 4096

#: An org pool far larger than any cost here, so only the lease chunk can refuse on the admission
#: clause (clause 3); everywhere else the pool never binds.
_POOL = 10_000_000
#: A lease chunk far larger than any cost here (the refund cases must be admitted by budget).
_BIG_CHUNK = 1_000_000
#: The measured guard rate the request-count chunk is derived from (as in test_v2_leaks_budget).
_Q_SAFE = 1000.0

#: An input value the input detector double flags ``card`` (BLOCK under MinimalOutputResolver).
_CARD = "4111111111111111"
#: A marker the forcing REDACT detector fires on (drives ``input_unmaskable``).
_PROBE = "forceunmaskable"
#: An out-of-range REDACT finding span: far beyond any request content, so the resolver's REDACT
#: transformation over it cannot be verified -> VerificationFailure -> input_unmaskable.
_FAR_START = 1_000_000
_FAR_END = _FAR_START + 11


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
# Seeded request generator: random messages, max_tokens and n.
# --------------------------------------------------------------------------- #

_WORDS = ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel")
#: Token-field draws. All stay at or below ``max_output_tokens_boundary()`` (== 1_000_000) so
#: parse never refuses -- the boundary refusal is Property 16 / task 34.2, not this surface half.
_MAX_TOKENS_CHOICES = (0, 1, 16, 100, 256, 4096, 8192, 65536, 100_000, 1_000_000)
_N_CHOICES = (1, 1, 1, 2, 3, 4, 8, 16)


def _text(rng: random.Random, words: int) -> str:
    return " ".join(rng.choice(_WORDS) for _ in range(words))


def _string_message(rng: random.Random) -> dict[str, object]:
    role = rng.choice(("user", "system", "assistant"))
    return {"role": role, "content": _text(rng, rng.randint(1, 20))}


def _parts_message(rng: random.Random) -> dict[str, object]:
    parts = [
        {"type": "text", "text": _text(rng, rng.randint(1, 12))}
        for _ in range(rng.randint(1, 4))
    ]
    return {"role": rng.choice(("user", "system")), "content": parts}


def _tool_message(rng: random.Random) -> dict[str, object]:
    """A message carrying ``tool_calls`` -- its ``tool_arguments`` text is NOT counted by v1's
    message-only oracle, so a request containing it charges STRICTLY MORE than the oracle."""
    arguments = json.dumps({"q": _text(rng, rng.randint(1, 8))})
    return {
        "role": "assistant",
        "content": _text(rng, rng.randint(0, 6)),
        "tool_calls": [{"type": "function", "function": {"name": "f", "arguments": arguments}}],
    }


def _draw_request(rng: random.Random) -> tuple[dict[str, object], bool]:
    """Return ``(body, message_only)``: a chat body and whether its only input text is message
    content (string or content-part). ``message_only`` requests must charge the v1 oracle exactly;
    the rest (tool-bearing) charge at least it."""
    count = rng.randint(1, 5)
    message_only = rng.random() < 0.7
    messages: list[dict[str, object]] = []
    for _ in range(count):
        if message_only:
            messages.append(rng.choice((_string_message, _parts_message))(rng))
        else:
            messages.append(
                rng.choice((_string_message, _parts_message, _tool_message))(rng)
            )
    # When not forced message-only, guarantee at least one tool message so the >= clause is real.
    if not message_only and not any("tool_calls" in m for m in messages):
        messages.append(_tool_message(rng))
    body: dict[str, object] = {"model": _MODEL, "messages": messages}
    max_tokens = rng.choice(_MAX_TOKENS_CHOICES)
    if max_tokens or rng.random() < 0.5:
        body["max_tokens"] = max_tokens
    n = rng.choice(_N_CHOICES)
    if n != 1 or rng.random() < 0.5:
        body["n"] = n
    actually_message_only = not any("tool_calls" in m for m in messages)
    return body, actually_message_only


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
    """Admits every request and records the ``cost`` the lifecycle charges through ``evaluate``."""

    __slots__ = ("costs", "refunds")

    def __init__(self) -> None:
        self.costs: list[int] = []
        self.refunds: list[int] = []

    async def evaluate(self, cost: int, *, request_id: str) -> BudgetVerdict | Admitted:
        self.costs.append(cost)
        return Admitted(owner_id=_ORG, request_id=request_id, admitted_at=0.0)

    def refund(self, cost: int, *, request_id: str) -> None:
        del request_id
        self.refunds.append(cost)


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


def _block_detector(text: str) -> Sequence[Finding]:
    """Flags the card value as a CREDENTIAL finding (BLOCK under MinimalOutputResolver)."""
    if _CARD not in text:
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


def _unmaskable_detector(text: str) -> Sequence[Finding]:
    """One out-of-range REDACT finding when the probe marker is present (drives input_unmaskable).

    ``det`` is an UNKNOWN detector so MinimalOutputResolver maps it to REDACT (a known class like
    ``card`` would map to BLOCK). The span is far beyond any request content, so the resolver's
    REDACT transformation cannot be verified -> VerificationFailure -> input_unmaskable.
    """
    if _PROBE not in text:
        return ()
    return (
        Finding(
            detector="det",
            detector_version="v2-leaks-f",
            category=Category.PII,
            status=FindingStatus.EXECUTED,
            confidence=1.0,
            spans=(Span(start=_FAR_START, end=_FAR_END),),
            evidence=None,
        ),
    )


def _raise_detector(_text: str) -> Sequence[Finding]:
    raise RuntimeError("v2-leaks-f: scanner unavailable")


def _clean_detector(_text: str) -> Sequence[Finding]:
    return ()


def _edge_lease(client: Any, contract: ResourceContract, *, chunk: int) -> BudgetLease:
    """A real BudgetLease wired the way the design's edge wiring constructs it."""
    return BudgetLease(
        client,
        LeaseConfig(org=_ORG, chunk=chunk, low_watermark=derive_low_watermark(chunk), ttl_s=30.0),
        worker_id="w0",
        clock=lambda: 0.0,
        spawn=_drop,
        metrics=_QuotaMetrics(),
        overdraft_tokens=contract.max_request_tokens(),
    )


async def _real_quota(
    client: Any, contract: ResourceContract, *, chunk: int
) -> tuple[QuotaComponent, BudgetLease]:
    """A REAL QuotaComponent over a REAL BudgetLease on ``client``, first chunk acquired."""
    await client.set(KEYS.budget_remaining(_ORG), _POOL)
    await client.set(KEYS.budget_generation(_ORG), 0)
    lease = _edge_lease(client, contract, chunk=chunk)
    assert await lease.acquire(0), "harness: the first chunk was not granted"
    gcra = LocalGCRA(GcraParams(rate_per_s=1_000_000.0, burst=1_000_000), clock=lambda: 0.0)
    quota = QuotaComponent(gcra, lease, generation_source=lambda: 0, metrics=_QuotaMetrics())
    return quota, lease


def _app(
    *,
    quota: BudgetDecider,
    admission: _Admission,
    provider: _Provider,
    input_detector: object,
) -> ASGIApplication:
    return build_app(
        contract=_contract(),
        provider=provider,
        scanner=cast(ScanProtocol, holdback_scan),
        resolver=MinimalOutputResolver(),
        metrics_registry=_Metrics(),
        clock=lambda: 0.0,
        control=_Control(),
        error_scanner=_clean_detector,
        input_detector=cast(Any, input_detector),
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


def _encode(obj: Mapping[str, object]) -> bytes:
    return json.dumps(obj).encode("utf-8")


# --------------------------------------------------------------------------- #
# The expected surface charge, recomputed from the parsed + clamped request.
# --------------------------------------------------------------------------- #


def _chat_charge(contract: ResourceContract, body: Mapping[str, object]) -> int:
    """The chat surface charge: ``estimate_request_tokens(prompt_chars, clamped mt, clamped n)``.

    Reproduces the surface exactly: parse the body, clamp the output budget against
    ``default_response_token_ceiling()``, then charge the contract estimate over the clamped
    request. This IS ``ChatCompletionsSurface._estimated_cost`` over the clamped ``req``.
    """
    parser = OpenAIRequestParser()
    req = parser.parse(_encode(body), surface=Surface.CHAT, contract=contract)
    clamped = clamp_output_budget(
        req, org_ceiling=contract.default_response_token_ceiling(), contract=contract
    )
    return contract.estimate_request_tokens(
        prompt_chars(clamped), clamped.max_tokens, clamped.n
    )


# --------------------------------------------------------------------------- #
# The property: the charge is the contract estimate over the clamped budget; v1 parity /
# lower bound; the end-to-end charge equals the recompute.
# --------------------------------------------------------------------------- #


def test_surface_charges_the_contract_estimate_over_the_clamped_budget() -> None:
    """Property 12 (surface half): every surface charges the contract estimate; v1 parity (F).

    For 10,000 seeded ``(messages, max_tokens, n)`` chat requests:

    * the charge equals ``estimate_request_tokens(prompt_chars(clamped), clamped max_tokens,
      clamped n)`` (clause 1), and never exceeds ``contract.max_request_tokens()``;
    * on a message-content-only request the charge equals the frozen v1 oracle applied after v1's
      clamp (clause 2); on a tool/function-bearing request it is >= the oracle;
    * every ``_E2E_EVERY``-th request drives the full lifecycle with a recording budget and the
      cost the budget actually received equals the recompute -- so the surface really charges it.

    **Validates: Requirements 2.15, 2.16, 2.18**
    """
    rng = random.Random(_SEED)
    contract = _contract()
    upper = contract.max_request_tokens()

    message_only_seen = 0
    tool_bearing_seen = 0
    strictly_above_oracle_seen = 0
    e2e_cases = 0
    e2e_message_only = 0
    e2e_tool_bearing = 0

    for index in range(_ITERATIONS):
        body, message_only = _draw_request(rng)
        where = f"seed={_SEED:#x} i={index} body={body!r}"

        charge = _chat_charge(contract, body)
        oracle = v1_estimate(body, max_response_tokens=DEFAULT_MAX_RESPONSE_TOKENS)

        # Clause 1: the charge never exceeds the per-request upper bound.
        assert charge <= upper, f"{where}: charge {charge} exceeds max_request_tokens {upper}"
        assert charge >= 1, f"{where}: charge {charge} below the minimum of 1"

        if message_only:
            message_only_seen += 1
            # Clause 2 (parity): prompt_chars reproduces v1's folded length and clamp_output_budget
            # reproduces v1's n / max_tokens clamp, so the charge equals the v1 oracle exactly.
            assert charge == oracle, (
                f"{where}: message-only charge {charge} != v1 oracle {oracle}"
            )
        else:
            tool_bearing_seen += 1
            # Clause 2 (lower bound): tool/function argument text is counted by prompt_chars but
            # not by v1's message-only oracle, so the charge is >= the oracle.
            assert charge >= oracle, (
                f"{where}: tool-bearing charge {charge} < v1 oracle {oracle}"
            )
            if charge > oracle:
                strictly_above_oracle_seen += 1

        # The end-to-end tier: the cost the surface ACTUALLY charges equals the recompute.
        if index % _E2E_EVERY == 0:
            budget = _RecordingBudget()

            async def _run(body: Mapping[str, object] = body) -> tuple[int, dict[str, Any]]:
                app = _app(
                    quota=budget,
                    admission=_Admission(),
                    provider=_Provider(),
                    input_detector=_clean_detector,
                )
                return await _serve(app, _CHAT, _encode(body))

            status, error = asyncio.run(_run())
            e2e_cases += 1
            if message_only:
                e2e_message_only += 1
            else:
                e2e_tool_bearing += 1
            assert status == 200, f"{where} (e2e): status {status} error {error!r}"
            assert budget.costs == [charge], (
                f"{where} (e2e): surface charged {budget.costs!r}, recompute {charge}"
            )

    # Coverage (asserted non-trivial): both request shapes, a genuinely-above-oracle case, and an
    # end-to-end case on each shape, were exercised across the run.
    assert message_only_seen > 0 and tool_bearing_seen > 0, (
        f"seed={_SEED:#x}: both shapes must occur, "
        f"message-only={message_only_seen} tool-bearing={tool_bearing_seen}"
    )
    assert strictly_above_oracle_seen > 0, (
        f"seed={_SEED:#x}: no tool-bearing request charged strictly above the oracle "
        f"(the >= clause was never non-trivially exercised)"
    )
    assert e2e_cases >= 2, f"seed={_SEED:#x}: expected >= 2 end-to-end cases, ran {e2e_cases}"
    assert e2e_message_only > 0 and e2e_tool_bearing > 0, (
        f"seed={_SEED:#x}: end-to-end tier must cover both shapes, "
        f"message-only={e2e_message_only} tool-bearing={e2e_tool_bearing}"
    )


# --------------------------------------------------------------------------- #
# Clause 3: a cost larger than one lease chunk but within the org budget is ADMITTED.
# --------------------------------------------------------------------------- #

#: A 2,000-character plain-ASCII prompt (its token cost far exceeds the request-count chunk).
_PROMPT_2000 = ("lorem ipsum dolor sit amet " * 80)[:2000]


def _serve_over_real_lease(
    chunk: int, body: bytes, path: str = _CHAT
) -> tuple[int, dict[str, Any], int]:
    async def _run() -> tuple[int, dict[str, Any], int]:
        client = fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer(), decode_responses=True)
        quota, _lease = await _real_quota(client, _contract(), chunk=chunk)
        provider = _Provider()
        app = _app(
            quota=quota,
            admission=_Admission(),
            provider=provider,
            input_detector=_clean_detector,
        )
        status, error = await _serve(app, path, body)
        await client.aclose()
        return status, error, provider.opened

    return asyncio.run(_run())


def test_cost_above_one_chunk_but_within_budget_is_admitted() -> None:
    """A per-token cost larger than one request-count chunk, within the pool, is served (clause 3).

    **Validates: Requirements 2.15, 2.16**
    """
    chunk = derive_lease_chunk(_contract(), q_safe=_Q_SAFE)
    body = {"model": _MODEL, "messages": [{"role": "user", "content": _PROMPT_2000}]}
    cost = _chat_charge(_contract(), body)
    assert chunk < cost, f"harness: chunk {chunk} must be below the cost {cost}"
    status, error, opened = _serve_over_real_lease(chunk, _encode(body))
    assert error.get("code") != BUDGET_UNAVAILABLE, (
        f"chunk={chunk} cost={cost}: a long prompt was refused {error!r} with the pool at {_POOL}"
    )
    assert (status, opened) == (200, 1), (
        f"chunk={chunk} cost={cost}: status {status} error {error!r}"
    )


# --------------------------------------------------------------------------- #
# Clause 4: input BLOCK, scan failure and input_unmaskable each REFUND the spend.
# --------------------------------------------------------------------------- #


def _blocked_body(path: str, kind: str) -> bytes:
    """A body that drives ``kind`` on ``path``. ``kind`` in {block, scan_failure, unmaskable}."""
    if kind == "unmaskable":
        content = f"please handle {_PROBE} now"
    elif kind == "block":
        content = f"pay with {_CARD} please"
    else:  # scan_failure -- the detector raises regardless of content
        content = "anything at all"
    if path == _EMBEDDINGS:
        return _encode({"model": "echo-1", "input": [content]})
    return _encode({"model": _MODEL, "messages": [{"role": "user", "content": content}]})


def _detector_for(kind: str) -> object:
    if kind == "unmaskable":
        return _unmaskable_detector
    if kind == "scan_failure":
        return _raise_detector
    return _block_detector


_EXPECTED_CODE = {
    "block": REFUSE_INPUT_BLOCKED,
    "scan_failure": REFUSE_INPUT_SCAN_FAILURE,
    "unmaskable": REFUSE_INPUT_UNMASKABLE,
}


def _refund_case(path: str, kind: str) -> tuple[int, dict[str, Any], int, int, int, list[int]]:
    """Serve a zero-dispatch refusal over a real lease.

    Returns status, error, held before/after, provider opens, and an (unused) refunds list -- the
    refund is observed through the real lease ``remaining`` ledger, not a double.
    """

    async def _run() -> tuple[int, dict[str, Any], int, int, int, list[int]]:
        client = fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer(), decode_responses=True)
        quota, lease = await _real_quota(client, _contract(), chunk=_BIG_CHUNK)
        held_before = lease.remaining
        provider = _Provider()
        app = _app(
            quota=quota,
            admission=_Admission(),
            provider=provider,
            input_detector=_detector_for(kind),
        )
        status, error = await _serve(app, path, _blocked_body(path, kind))
        held_after = lease.remaining
        await client.aclose()
        return status, error, held_before, held_after, provider.opened, []

    return asyncio.run(_run())


def test_zero_dispatch_refusals_refund_the_spend() -> None:
    """Input BLOCK, scan failure and ``input_unmaskable`` refund the spend, chat and embeddings.

    Each refusal spends budget then opens no provider, so ``RequestHolds.release`` refunds the
    spend in the handler's ``finally``: the real lease ``remaining`` is restored to its pre-spend
    value (C29, 2.17). Driven over a REAL ``QuotaComponent`` / ``BudgetLease`` ledger.

    **Validates: Requirements 2.17, 2.18**
    """
    for path in (_CHAT, _EMBEDDINGS):
        for kind in ("block", "scan_failure", "unmaskable"):
            status, error, before, after, opened, _refunds = _refund_case(path, kind)
            where = f"seed={_SEED:#x} path={path} kind={kind} status={status} error={error!r}"
            assert error.get("code") == _EXPECTED_CODE[kind], (
                f"{where}: expected refuse code {_EXPECTED_CODE[kind]!r}"
            )
            assert opened == 0, f"{where}: provider opened {opened} time(s), expected 0"
            assert after == before, (
                f"{where}: spend not refunded -- lease held {before} before, {after} after"
            )
