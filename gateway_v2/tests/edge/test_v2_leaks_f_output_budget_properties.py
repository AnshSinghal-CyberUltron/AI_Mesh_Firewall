# Feature: v2-enforcement-leaks, task 34.2 — Group F output-budget clamp property test
# Validates: Requirements 2.15, 2.16, 2.18
"""Property 16 (Bug Condition F): the output budget is clamped AND bounded (v1 parity).

Fix-checking property test (spec ``v2-enforcement-leaks``, task 34.2; design Property 16). Unlike
the Group F EXPLORATION test (``tests/admit/test_v2_leaks_budget.py``, task 9, strict-xfail on the
unfixed tree), this file is written against the ALREADY-FIXED tree and MUST pass: it is the
10,000-iteration generalisation of the example-based wire unit test
(``tests/edge/test_v2_leaks_wire.py``, task 24.3) plus the refused-case lifecycle check, the
four dev-profile integration cases and the envelope unit for the two boundary codes.

The property (design Property 16). For any chat request whose ``max_tokens`` /
``max_completion_tokens`` / ``n`` are drawn from absent, JSON ``null``, ``0``, an in-range value,
values around 4096 / 65536 / 1_000_000, a value above the boundary, and off-contract values (bool,
float, string, negative, ``n = 0``), and any org ceiling >= 1:

* JSON ``null`` parses identically to an absent field;
* a ``max_tokens`` above the parse boundary refuses ``invalid_max_tokens`` and a
  ``max_completion_tokens`` above it refuses ``invalid_max_completion_tokens`` — each HTTP 400
  ``invalid_request_error`` with ``param`` naming the field and ``request_id`` null — and the
  request is NOT admitted, makes ZERO provider calls and spends NO budget;
* an off-contract value refuses ``MALFORMED_WRONG_SHAPE``;
* otherwise the clamped ``max_tokens == min(parsed, org_ceiling, max_output_tokens())`` (so
  ``<= 65536``) and ``n == min(parsed, max_completions())`` (so ``1``);
* the clamp is idempotent (``request_clamps`` included);
* ``request_clamps`` holds exactly one entry per CHANGED field, in v1's ``"<field>=<pre>-><post>"``
  form, none for an unchanged field;
* the charged cost ``estimate_request_tokens(prompt_chars, clamped max_tokens, clamped n)`` is at
  most ``contract.max_request_tokens()``.

Seams. The parse + clamp are the PURE wire transforms ``OpenAIRequestParser.parse`` +
``clamp_output_budget`` the lifecycle calls (``edge/openai_chat.py`` parses, clamps with
``org_ceiling=contract.default_response_token_ceiling()``, then charges
``estimate_request_tokens(prompt_chars(req), req.max_tokens, req.n)``); this file drives those same
functions directly for the 10,000-iteration property so the assertion is independent of the
transport. The refused-case lifecycle check and the integration cases then drive the REAL ASGI app
end to end to prove the refusal makes zero provider calls / zero admits / spends no budget, and the
admitted case charges the v1 cost.

Property idiom (house convention): seeded ``random.Random``, 10,000 iterations, the seed in every
assertion message, NO hypothesis, ``asyncio.run`` for the async lifecycle driver.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any, cast

import fakeredis

from gateway_v2.admit.gcra import GcraParams, LocalGCRA
from gateway_v2.admit.grant import Admitted, BudgetVerdict, ShedVerdict
from gateway_v2.admit.lease import BudgetLease, LeaseConfig
from gateway_v2.admit.quota import QuotaComponent, derive_low_watermark
from gateway_v2.detect.holdback import scan as holdback_scan
from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest
from gateway_v2.domain.plan import Surface
from gateway_v2.edge import errors, serve
from gateway_v2.edge.app import ASGIApplication, build_app
from gateway_v2.edge.cancel import InFlightSnapshot
from gateway_v2.edge.chat_deps import BudgetDecider
from gateway_v2.edge.wire.openai import (
    INVALID_MAX_COMPLETION_TOKENS,
    INVALID_MAX_TOKENS,
    MALFORMED_WRONG_SHAPE,
    MalformedRequest,
    OpenAIRequestParser,
    clamp_output_budget,
    prompt_chars,
)
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
# House idiom: seeded random, >= 10,000 iterations, seed in every message.
# --------------------------------------------------------------------------- #

ITERATIONS = 10_000
SEED = 0xF00D_B16E

_PARSER = OpenAIRequestParser()
_ORG = DEFAULT_ORG_ID
_CHAT = "/v1/chat/completions"
_MODEL = "gpt-4o-mini"

# The dev-profile integration boot (same shape as the Group A property test).
EXAMPLE_PLAN = Path(__file__).resolve().parents[2] / "dev" / "plan.example.json"
KEY = "sk-dev-pipeline"


def _contract() -> ResourceContract:
    """A serviceable contract; the Group F accessors return the v1-parity bounds regardless."""
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


_CONTRACT = _contract()
_BOUNDARY = _CONTRACT.max_output_tokens_boundary()
_HARD_CAP = _CONTRACT.max_output_tokens()
_MAX_COMPLETIONS = _CONTRACT.max_completions()
_MAX_REQUEST_TOKENS = _CONTRACT.max_request_tokens()


# --------------------------------------------------------------------------- #
# The value matrix (design Property 16): one draw per field.
# --------------------------------------------------------------------------- #


class _Draw:
    """One drawn field value: the JSON it serialises to + the parse/clamp intent it carries.

    ``present`` is False for the absent case; ``json_value`` is what goes into the body when
    present (a JSON ``null`` carries ``json_value is None`` with ``present`` True). ``off_contract``
    marks a bool/float/string/negative/``n=0`` draw (refuse ``MALFORMED_WRONG_SHAPE``);
    ``over_boundary`` marks a well-shaped int above the boundary (the field-specific refusal).
    ``parsed`` is the int the parser yields for an accepted draw (the default for absent/null).
    """

    __slots__ = ("present", "json_value", "off_contract", "over_boundary", "parsed")

    def __init__(
        self,
        *,
        present: bool,
        json_value: object,
        off_contract: bool,
        over_boundary: bool,
        parsed: int,
    ) -> None:
        self.present = present
        self.json_value = json_value
        self.off_contract = off_contract
        self.over_boundary = over_boundary
        self.parsed = parsed


# The off-contract shapes the parser refuses with MALFORMED_WRONG_SHAPE (bool before int; a float
# incl. the integral 4096.0; a string; a negative int). ``n == 0`` is handled per-field (it is
# below n's minimum of 1 → WRONG_SHAPE, but it is a VALID max_tokens of 0).
_OFF_CONTRACT_SHARED: tuple[object, ...] = (True, False, 4096.0, 65536.0, "100", "4096", -1, -4096)


def _draw_tokens(rng: random.Random) -> _Draw:
    """Draw a ``max_tokens`` / ``max_completion_tokens`` value from the full matrix.

    ``0`` is a VALID value here (the scan-only sentinel), so it is NOT off-contract for the token
    fields. In-range + around-the-landmarks values are accepted ints; above-boundary values carry
    the field-specific refusal; the shared off-contract shapes carry WRONG_SHAPE.
    """
    kind = rng.randrange(8)
    if kind == 0:
        return _Draw(
            present=False, json_value=None, off_contract=False, over_boundary=False, parsed=0
        )
    if kind == 1:  # explicit JSON null → absent
        return _Draw(
            present=True, json_value=None, off_contract=False, over_boundary=False, parsed=0
        )
    if kind == 2:  # off-contract shape
        value = rng.choice(_OFF_CONTRACT_SHARED)
        return _Draw(
            present=True, json_value=value, off_contract=True, over_boundary=False, parsed=0
        )
    if kind == 3:  # above the boundary
        value = rng.randint(_BOUNDARY + 1, _BOUNDARY + 2_000_000)
        return _Draw(
            present=True, json_value=value, off_contract=False, over_boundary=True, parsed=value
        )
    # Accepted int: 0, small, around 4096, around 65536, up to exactly the boundary.
    value = rng.choice(
        (
            0,
            rng.randint(1, 4095),
            rng.choice((4095, 4096, 4097)),
            rng.choice((65535, 65536, 65537)),
            rng.randint(4096, 65536),
            rng.randint(65536, _BOUNDARY - 1),
            _BOUNDARY,
        )
    )
    return _Draw(
        present=True, json_value=value, off_contract=False, over_boundary=False, parsed=value
    )


def _draw_n(rng: random.Random) -> _Draw:
    """Draw an ``n`` value. ``n``'s minimum is 1, so ``0`` / negatives / non-ints are WRONG_SHAPE.

    ``n`` has no upper boundary at parse (the clamp forces it to ``max_completions()``), so there
    is no over-boundary case for ``n``.
    """
    kind = rng.randrange(6)
    if kind == 0:
        return _Draw(
            present=False, json_value=None, off_contract=False, over_boundary=False, parsed=1
        )
    if kind == 1:  # explicit null → absent (default 1)
        return _Draw(
            present=True, json_value=None, off_contract=False, over_boundary=False, parsed=1
        )
    if kind == 2:  # off-contract: bool/float/str/negative/zero (below the n-min of 1 or wrong type)
        value = rng.choice((True, False, 2.0, "4", -1, 0))
        return _Draw(
            present=True, json_value=value, off_contract=True, over_boundary=False, parsed=0
        )
    # Accepted int >= 1: 1, small, around the completions cap, large.
    value = rng.choice((1, rng.randint(1, 8), rng.randint(2, 64), rng.randint(1, 1000)))
    return _Draw(
        present=True, json_value=value, off_contract=False, over_boundary=False, parsed=value
    )


def _body(mt: _Draw, mct: _Draw, n: _Draw) -> dict[str, object]:
    """Assemble a minimal valid chat body, adding each present output-budget field."""
    body: dict[str, object] = {
        "model": _MODEL,
        "messages": [{"role": "user", "content": "please summarise the account notes"}],
    }
    if mt.present:
        body["max_tokens"] = mt.json_value
    if mct.present:
        body["max_completion_tokens"] = mct.json_value
    if n.present:
        body["n"] = n.json_value
    return body


# --------------------------------------------------------------------------- #
# The property.
# --------------------------------------------------------------------------- #


def test_output_budget_is_clamped_and_bounded_property() -> None:
    """Property 16: parse + clamp are v1-faithful, idempotent and bounded.

    **Validates: Requirements 2.15, 2.16, 2.18**
    """
    rng = random.Random(SEED)

    # Coverage counters, asserted non-trivial so a green run is not vacuous.
    null_cases = 0
    mt_over = 0
    mct_over = 0
    off_contract = 0
    clamped_mt = 0
    clamped_n = 0
    accepted = 0

    for iteration in range(ITERATIONS):
        mt = _draw_tokens(rng)
        mct = _draw_tokens(rng)
        n = _draw_n(rng)
        org_ceiling = rng.randint(1, 2_000_000)
        tag = (
            f"seed={SEED:#x} iter={iteration} org_ceiling={org_ceiling} "
            f"mt={mt.json_value!r}/{mt.present} mct={mct.json_value!r}/{mct.present} "
            f"n={n.json_value!r}/{n.present}"
        )
        body = _body(mt, mct, n)
        raw = json.dumps(body).encode("utf-8")

        # ---- Refusals -----------------------------------------------------
        # The parser evaluates max_tokens, then n, then max_completion_tokens (see
        # OpenAIRequestParser.parse). The FIRST failing field in that order governs the raise.
        expected_refusal = _expected_refusal(mt, n, mct)
        if expected_refusal is not None:
            code, param = expected_refusal
            try:
                _PARSER.parse(raw, surface=Surface.CHAT, contract=_CONTRACT)
            except MalformedRequest as exc:
                assert exc.code == code, f"{tag}: refuse code {exc.code!r} != {code!r}"
                assert exc.param == param, f"{tag}: refuse param {exc.param!r} != {param!r}"
            else:  # pragma: no cover - a missing refusal is a hard property failure
                raise AssertionError(f"{tag}: expected refusal {code!r}, parse succeeded")
            if code in (INVALID_MAX_TOKENS, INVALID_MAX_COMPLETION_TOKENS):
                if code == INVALID_MAX_TOKENS:
                    mt_over += 1
                else:
                    mct_over += 1
            else:
                off_contract += 1
            continue

        # ---- Accepted: parse, then clamp ----------------------------------
        req = _PARSER.parse(raw, surface=Surface.CHAT, contract=_CONTRACT)
        accepted += 1

        # null parses identically to absent: the parser yields the field defaults.
        assert req.max_tokens == mt.parsed, (
            f"{tag}: parsed max_tokens {req.max_tokens} != {mt.parsed}"
        )
        assert req.n == n.parsed, f"{tag}: parsed n {req.n} != {n.parsed}"
        if mt.present and mt.json_value is None:
            null_cases += 1

        clamped = clamp_output_budget(req, org_ceiling=org_ceiling, contract=_CONTRACT)

        # The clamp formula (design Property 16).
        expected_mt = min(req.max_tokens, org_ceiling, _HARD_CAP)
        expected_n = min(req.n, _MAX_COMPLETIONS)
        assert clamped.max_tokens == expected_mt, (
            f"{tag}: clamped max_tokens {clamped.max_tokens} != min({req.max_tokens}, "
            f"{org_ceiling}, {_HARD_CAP}) = {expected_mt}"
        )
        assert clamped.n == expected_n == 1, (
            f"{tag}: clamped n {clamped.n} != min({req.n}, {_MAX_COMPLETIONS}) = {expected_n}"
        )
        assert clamped.max_tokens <= _HARD_CAP, f"{tag}: clamped max_tokens exceeds the hard cap"

        # request_clamps: exactly one entry per CHANGED field, v1 order (n then max_tokens).
        expected_clamps: list[str] = []
        if expected_n != req.n:
            expected_clamps.append(f"n={req.n}->{expected_n}")
            clamped_n += 1
        if expected_mt != req.max_tokens:
            expected_clamps.append(f"max_tokens={req.max_tokens}->{expected_mt}")
            clamped_mt += 1
        assert clamped.request_clamps == tuple(expected_clamps), (
            f"{tag}: request_clamps {clamped.request_clamps!r} != {tuple(expected_clamps)!r}"
        )

        # Idempotence: clamping a clamped request returns an equal request (clamps included).
        again = clamp_output_budget(clamped, org_ceiling=org_ceiling, contract=_CONTRACT)
        assert again.max_tokens == clamped.max_tokens, f"{tag}: idempotence broke max_tokens"
        assert again.n == clamped.n, f"{tag}: idempotence broke n"
        assert again.request_clamps == (), (
            f"{tag}: second clamp recorded {again.request_clamps!r} (must be empty)"
        )

        # max_completion_tokens is validated but never charged/clamped (v1 parity).
        assert clamped.max_completion_tokens == req.max_completion_tokens, (
            f"{tag}: max_completion_tokens changed under the clamp"
        )

        # The charged cost is v1's estimate over the clamped budget, bounded by max_request_tokens.
        chars = prompt_chars(clamped)
        cost = _CONTRACT.estimate_request_tokens(chars, clamped.max_tokens, clamped.n)
        assert cost <= _MAX_REQUEST_TOKENS, (
            f"{tag}: charged cost {cost} exceeds max_request_tokens() {_MAX_REQUEST_TOKENS}"
        )

    # Non-vacuity: each arm of the matrix is exercised a meaningful number of times.
    assert null_cases > 50, f"seed={SEED:#x}: too few explicit-null max_tokens cases ({null_cases})"
    assert mt_over > 50, f"seed={SEED:#x}: too few over-boundary max_tokens cases ({mt_over})"
    assert mct_over > 50, f"seed={SEED:#x}: too few over-boundary mct cases ({mct_over})"
    assert off_contract > 50, f"seed={SEED:#x}: too few off-contract cases ({off_contract})"
    assert clamped_mt > 50, f"seed={SEED:#x}: too few max_tokens-clamped cases ({clamped_mt})"
    assert clamped_n > 50, f"seed={SEED:#x}: too few n-clamped cases ({clamped_n})"
    assert accepted > 1000, f"seed={SEED:#x}: too few accepted cases ({accepted})"
    print(
        f"\n[v2-leaks-f-budget] seed={SEED:#x} iterations={ITERATIONS} accepted={accepted} "
        f"null={null_cases} mt_over={mt_over} mct_over={mct_over} off_contract={off_contract} "
        f"clamped_mt={clamped_mt} clamped_n={clamped_n}"
    )


def _expected_refusal(mt: _Draw, n: _Draw, mct: _Draw) -> tuple[str, str | None] | None:
    """The refusal the parser raises for this draw, in the parser's field order (mt, n, mct).

    ``OpenAIRequestParser.parse`` reads ``max_tokens`` (int field, over-boundary →
    INVALID_MAX_TOKENS; off-contract → WRONG_SHAPE), then ``n`` (off-contract → WRONG_SHAPE, no
    boundary), then ``max_completion_tokens`` (over-boundary → INVALID_MAX_COMPLETION_TOKENS;
    off-contract → WRONG_SHAPE). The FIRST failing field governs. Returns ``(code, param)`` or
    ``None`` when the draw parses cleanly.
    """
    if mt.off_contract:
        return (MALFORMED_WRONG_SHAPE, None)
    if mt.over_boundary:
        return (INVALID_MAX_TOKENS, "max_tokens")
    if n.off_contract:
        return (MALFORMED_WRONG_SHAPE, None)
    if mct.off_contract:
        return (MALFORMED_WRONG_SHAPE, None)
    if mct.over_boundary:
        return (INVALID_MAX_COMPLETION_TOKENS, "max_completion_tokens")
    return None


# --------------------------------------------------------------------------- #
# Refused-case lifecycle: zero provider calls, zero admits, unchanged lease.
# --------------------------------------------------------------------------- #
#
# Mirrors the in-process lifecycle driver of ``tests/admit/test_v2_leaks_budget.py`` (task 9): the
# REAL ``build_app`` lifecycle over a counting admission, a recording provider and a REAL
# ``QuotaComponent`` on a REAL ``BudgetLease`` over fakeredis. A parse refusal happens BEFORE
# admission / budget, so the refused case must leave all three untouched.

_POOL = 1_000_000
_CHUNK = 100_000


class _Admission:
    """Admits every request and counts admits; ``complete`` is a no-op (Group D owns completion)."""

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


class _Provider:
    """Echoes a fixed completion and counts opens (a refused request must never open it)."""

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
        return "# v2-leaks-f-budget\n"


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


def _drop(work: Any) -> None:
    """Off-path refill spawn that never runs (keeps the lease ledger deterministic)."""
    close = getattr(work, "close", None)
    if callable(close):
        close()


async def _real_quota(client: Any) -> tuple[QuotaComponent, BudgetLease]:
    """A REAL QuotaComponent over a REAL BudgetLease on ``client``, first chunk acquired."""
    await client.set(KEYS.budget_remaining(_ORG), _POOL)
    await client.set(KEYS.budget_generation(_ORG), 0)
    metrics = _QuotaMetrics()
    lease = BudgetLease(
        client,
        LeaseConfig(
            org=_ORG, chunk=_CHUNK, low_watermark=derive_low_watermark(_CHUNK), ttl_s=30.0
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


def _app(*, quota: BudgetDecider, admission: _Admission, provider: _Provider) -> ASGIApplication:
    return build_app(
        contract=_CONTRACT,
        provider=provider,
        scanner=cast(ScanProtocol, holdback_scan),
        resolver=MinimalOutputResolver(),
        metrics_registry=_Metrics(),
        clock=lambda: 0.0,
        control=_Control(),
        error_scanner=lambda _text: (),
        input_detector=lambda _text: (),
        identity=ResolvingIdentity(),
        snapshot_source=warm_snapshot_source(),
        admission=admission,
        quota=quota,
    )


async def _serve(app: ASGIApplication, body: bytes) -> tuple[int, dict[str, Any]]:
    """One non-streaming chat request through the ASGI app; returns ``(status, error-object)``."""
    sent: list[Mapping[str, object]] = []
    incoming: list[Mapping[str, object]] = [
        {"type": "http.request", "body": body, "more_body": False},
    ]
    scope: dict[str, object] = {
        "type": "http",
        "method": "POST",
        "path": _CHAT,
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


def _refused_body(*, code: str) -> bytes:
    """A chat body whose parse refuses with ``code`` (an over-boundary or off-contract field)."""
    base: dict[str, object] = {
        "model": _MODEL,
        "messages": [{"role": "user", "content": "please summarise"}],
    }
    if code == INVALID_MAX_TOKENS:
        base["max_tokens"] = _BOUNDARY + 1
    elif code == INVALID_MAX_COMPLETION_TOKENS:
        base["max_completion_tokens"] = _BOUNDARY + 1
    else:  # MALFORMED_WRONG_SHAPE
        base["max_tokens"] = "not-an-int"
    return json.dumps(base).encode("utf-8")


def test_refused_requests_touch_no_admission_budget_or_provider() -> None:
    """Each parse refusal makes zero provider calls, zero admits and leaves the lease unchanged.

    **Validates: Requirements 2.15, 2.16, 2.18**
    """
    for code in (INVALID_MAX_TOKENS, INVALID_MAX_COMPLETION_TOKENS, MALFORMED_WRONG_SHAPE):

        async def _run(code: str = code) -> tuple[int, dict[str, Any], int, int, int]:
            client = fakeredis.FakeAsyncRedis(
                server=fakeredis.FakeServer(), decode_responses=True
            )
            quota, lease = await _real_quota(client)
            held_before = lease.remaining
            provider = _Provider()
            admission = _Admission()
            app = _app(quota=quota, admission=admission, provider=provider)
            status, error = await _serve(app, _refused_body(code=code))
            held_after = lease.remaining
            await client.aclose()
            return status, error, len(admission.admitted), provider.opened, held_after - held_before

        status, error, admits, opened, held_delta = asyncio.run(_run())
        tag = f"seed={SEED:#x} code={code}"
        # Every refused request is untouched by admission / budget / provider — the parse refusal
        # happens before those phases, so a boundary refusal and an off-contract refusal alike make
        # zero admits, zero provider opens and move the lease by zero.
        assert error.get("code") == code, f"{tag}: error code {error.get('code')!r} != {code!r}"
        assert error.get("request_id") is None, f"{tag}: request_id {error.get('request_id')!r}"
        assert admits == 0, f"{tag}: {admits} admits on a refused request"
        assert opened == 0, f"{tag}: {opened} provider opens on a refused request"
        assert held_delta == 0, f"{tag}: lease moved by {held_delta} on a refused request"
        # The two boundary codes render the v1 HTTP 400 ``invalid_request_error`` family and name
        # the offending field. ``MALFORMED_WRONG_SHAPE`` is an unmapped off-contract code: it
        # renders the declared generic error (HTTP 500, no field-specific ``param``) — this clause
        # only asserts the refusal-before-work invariant for it, not the 400 family.
        if code == INVALID_MAX_TOKENS:
            assert status == 400, f"{tag}: status {status} error {error!r}"
            assert error.get("param") == "max_tokens", f"{tag}: param {error.get('param')!r}"
        elif code == INVALID_MAX_COMPLETION_TOKENS:
            assert status == 400, f"{tag}: status {status} error {error!r}"
            assert error.get("param") == "max_completion_tokens", (
                f"{tag}: param {error.get('param')!r}"
            )


# --------------------------------------------------------------------------- #
# Dev-profile integration: streaming AND non-streaming, the four named cases.
# --------------------------------------------------------------------------- #


class _RecordingDevBudget:
    """Wraps the dev ``BudgetDecider`` to record the ``cost`` the lifecycle charges.

    Swapped into the dev-profile ``deps`` the way the Group A property test swaps the provider, so
    the integration admitted case can assert the charged v1 cost while the REAL dev pipeline
    (identity → plan → admission → budget → scan → provider → output guard) runs end to end.
    """

    __slots__ = ("_inner", "costs")

    def __init__(self, inner: BudgetDecider) -> None:
        self._inner = inner
        self.costs: list[int] = []

    async def evaluate(self, cost: int, *, request_id: str) -> BudgetVerdict | Admitted:
        self.costs.append(cost)
        return await self._inner.evaluate(cost, request_id=request_id)

    def refund(self, cost: int, *, request_id: str) -> None:
        self._inner.refund(cost, request_id=request_id)


def _dev_app(budget: _RecordingDevBudget | None) -> ASGIApplication:
    """Boot the REAL dev-profile app, optionally swapping in the recording budget."""
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
    if budget is not None:
        budget._inner = cast(BudgetDecider, deps["quota"])
        deps["quota"] = budget
    return serve._assemble_app(deps)


async def _dev_post(
    app: ASGIApplication, body: dict[str, object], *, stream: bool
) -> tuple[int, dict[str, Any]]:
    """One dev-profile chat request (streaming or not); returns ``(status, error-object)``.

    A parse boundary refusal renders as an HTTP body even for a ``stream: true`` body, because the
    refusal happens before a ``CanonicalRequest`` exists (``req is None`` in ``_render_refusal``),
    so the error is read off the HTTP JSON body in both modes.
    """
    payload = {"model": "echo-1", "stream": stream, **body}
    raw = json.dumps(payload).encode("utf-8")
    scope = {
        "type": "http",
        "method": "POST",
        "path": _CHAT,
        "headers": [(b"authorization", f"Bearer {KEY}".encode()), (b"content-type", b"json")],
    }
    sent: list[Mapping[str, object]] = []
    delivered = False

    async def _receive() -> Mapping[str, object]:
        nonlocal delivered
        if delivered:
            await asyncio.sleep(3600)
        delivered = True
        return {"type": "http.request", "body": raw, "more_body": False}

    async def _send(message: Mapping[str, object]) -> None:
        sent.append(dict(message))

    assert callable(app)
    await app(scope, _receive, _send)
    status = next(int(str(m["status"])) for m in sent if m["type"] == "http.response.start")
    chunks = b"".join(
        bytes(chunk)
        for m in sent
        if m["type"] == "http.response.body"
        and isinstance(chunk := m.get("body", b""), (bytes, bytearray))
    )
    error: dict[str, Any] = {}
    if chunks:
        try:
            decoded = json.loads(chunks)
        except (ValueError, UnicodeDecodeError):
            decoded = {}
        if isinstance(decoded, dict) and isinstance(decoded.get("error"), dict):
            error = decoded["error"]
    return status, error


def test_integration_max_tokens_over_boundary_refuses_400() -> None:
    """``max_tokens=2000000`` → 400 ``invalid_max_tokens`` (streaming AND non-streaming)."""
    app = _dev_app(None)
    for stream in (False, True):
        body = {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 2_000_000}
        status, error = asyncio.run(_dev_post(app, body, stream=stream))
        tag = f"seed={SEED:#x} stream={stream}"
        assert status == 400, f"{tag}: status {status} error {error!r}"
        assert error.get("code") == INVALID_MAX_TOKENS, f"{tag}: {error!r}"
        assert error.get("param") == "max_tokens", f"{tag}: {error!r}"
        assert error.get("request_id") is None, f"{tag}: {error!r}"


def test_integration_max_completion_tokens_over_boundary_refuses_400() -> None:
    """``max_completion_tokens=2000000`` → 400 ``invalid_max_completion_tokens`` (both modes)."""
    app = _dev_app(None)
    for stream in (False, True):
        body = {
            "messages": [{"role": "user", "content": "hi"}],
            "max_completion_tokens": 2_000_000,
        }
        status, error = asyncio.run(_dev_post(app, body, stream=stream))
        tag = f"seed={SEED:#x} stream={stream}"
        assert status == 400, f"{tag}: status {status} error {error!r}"
        assert error.get("code") == INVALID_MAX_COMPLETION_TOKENS, f"{tag}: {error!r}"
        assert error.get("param") == "max_completion_tokens", f"{tag}: {error!r}"
        assert error.get("request_id") is None, f"{tag}: {error!r}"


def test_integration_max_tokens_null_behaves_as_absent() -> None:
    """``max_tokens: null`` behaves as absent — the request is admitted and served (both modes)."""
    app = _dev_app(None)
    for stream in (False, True):
        body = {"messages": [{"role": "user", "content": "hi"}], "max_tokens": None}
        status, error = asyncio.run(_dev_post(app, body, stream=stream))
        tag = f"seed={SEED:#x} stream={stream}"
        assert status == 200, f"{tag}: null max_tokens should be served, got {status} {error!r}"


def test_integration_clamped_request_is_admitted_and_charged_v1_cost() -> None:
    """``max_tokens=100000, n=4`` is admitted, charged ``input + 4096``, with v1's clamp records.

    **Validates: Requirements 2.15, 2.16, 2.18**
    """
    budget = _RecordingDevBudget(cast(BudgetDecider, None))
    app = _dev_app(budget)
    content = "please summarise the account notes for me today"
    messages = [{"role": "user", "content": content}]
    status, error = asyncio.run(
        _dev_post(app, {"messages": messages, "max_tokens": 100_000, "n": 4}, stream=False)
    )
    tag = f"seed={SEED:#x}"
    assert status == 200, f"{tag}: status {status} error {error!r}"
    assert len(budget.costs) == 1, f"{tag}: exactly one budget charge, got {budget.costs!r}"

    # The clamp the lifecycle applies: org_ceiling == default_response_token_ceiling() (4096).
    org_ceiling = _CONTRACT.default_response_token_ceiling()
    parse_body = {"model": "echo-1", "messages": messages, "max_tokens": 100_000, "n": 4}
    req = _PARSER.parse(
        json.dumps(parse_body).encode("utf-8"), surface=Surface.CHAT, contract=_CONTRACT
    )
    clamped = clamp_output_budget(req, org_ceiling=org_ceiling, contract=_CONTRACT)
    # request_clamps is v1's format, n before max_tokens, one entry per changed field.
    assert clamped.request_clamps == ("n=4->1", f"max_tokens=100000->{org_ceiling}"), (
        f"{tag}: request_clamps {clamped.request_clamps!r}"
    )
    expected_input = max(1, prompt_chars(clamped) // 4)
    expected_cost = _CONTRACT.estimate_request_tokens(
        prompt_chars(clamped), clamped.max_tokens, clamped.n
    )
    assert expected_cost == expected_input + org_ceiling, (
        f"{tag}: expected cost {expected_cost} != input {expected_input} + {org_ceiling}"
    )
    assert budget.costs[0] == expected_cost, (
        f"{tag}: charged {budget.costs[0]} != v1 estimate {expected_cost} (input {expected_input} "
        f"+ clamped max_tokens {clamped.max_tokens})"
    )


# --------------------------------------------------------------------------- #
# Envelope unit: both boundary codes render 400, param set, request_id null.
# --------------------------------------------------------------------------- #


def test_invalid_max_tokens_envelope_renders_400_param_and_null_request_id() -> None:
    """``invalid_max_tokens`` → 400 invalid_request_error, ``param`` set, ``request_id`` null."""
    resp = errors.render_http("invalid_max_tokens", param="max_tokens")
    assert resp.status == 400, resp.status
    error = json.loads(resp.body.decode("utf-8"))["error"]
    assert error["type"] == "invalid_request_error", error
    assert error["code"] == "invalid_max_tokens", error
    assert error["param"] == "max_tokens", error
    assert error["request_id"] is None, error


def test_invalid_max_completion_tokens_envelope_renders_400_param_and_null_request_id() -> None:
    """The sibling code renders 400 with ``param`` set and ``request_id`` null."""
    resp = errors.render_http("invalid_max_completion_tokens", param="max_completion_tokens")
    assert resp.status == 400, resp.status
    error = json.loads(resp.body.decode("utf-8"))["error"]
    assert error["type"] == "invalid_request_error", error
    assert error["code"] == "invalid_max_completion_tokens", error
    assert error["param"] == "max_completion_tokens", error
    assert error["request_id"] is None, error
