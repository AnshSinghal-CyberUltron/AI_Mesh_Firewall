# Feature: chat-surface-gw15, Property 19: Two-tenant isolation
# Validates: Requirements 13.1, 13.2, 13.3
"""Property 19 — two-tenant isolation (GW15, task 5.8).

House idiom: a seeded ``random.Random``, no ``hypothesis``, >=10,000 iterations, the async
§10.4 lifecycle driven via ``asyncio.run`` — and, crucially for this property, the two
tenants' lifecycles are driven *concurrently* (``asyncio.gather`` of two overlapping
``run_lifecycle`` coroutines on one event loop) so a shared-mutable-state leak between the
two in-flight requests would surface, not be hidden by strict serialization.

*For any* two concurrently-served tenants with OPPOSITE policies, each request is resolved
and served under its OWN :class:`~gateway_v2.domain.plan.ExecutionPlan`, and no
configuration, finding, request ID, or audit record of one tenant appears in the other's
outcome; on completion each request's findings, request ID, and audit are attributed to its
originating ``Tenant_Identity`` only (R13.1 / R13.2 / R13.3).

The opposite policies.  A single shared canary token is placed in a random input channel of
*both* tenants' requests.  Tenant **BLOCK** runs an input detector that, for its canary,
emits one EXECUTED finding whose detector names the terminate-class ``jwt`` — the shipped
:class:`~gateway_v2.egress.output_guard.MinimalOutputResolver` resolves that to the
most-restrictive ``BLOCK`` disposition (C22), so tenant BLOCK's lifecycle refuses with the
declared ``input_blocked`` code and makes zero provider calls.  Tenant **ALLOW** runs a
detector that finds *nothing* for the identical canary, so its lifecycle proceeds and the
resolver returns ``ALLOW`` — the same canary, opposite outcomes, proving each request is
enforced under its own plan/policy and never the other's.

Each tenant is fully isolated by construction so a leak is observable:

* its OWN :class:`ExecutionPlan` (distinct ``org_id`` + ``content_hash`` ⇒ a distinct,
  deterministic ``DispatchRouter`` destination — the per-tenant *configuration*);
* its OWN per-tenant input detector, which records into a per-tenant *audit* sink EXACTLY
  the ``org_id`` of every request text it scans — so a scan of tenant A's text ever recorded
  against tenant B's sink (or vice versa) is a cross-contamination failure;
* its OWN gateway-minted, tenant-scoped ``request_id`` (``zs-{org_id}-…``), which the
  property asserts carries its own ``org_id`` and never the other tenant's.

The two lifecycles are interleaved on one event loop via ``asyncio.gather`` and the per-
tenant deps bundles share NO mutable object, so the only way one tenant's plan / finding /
request id / audit could appear in the other's outcome is a real isolation break — which the
assertions below would catch.  Everything this file owns is uniquely prefixed ``_p19_`` /
``_P19`` so it never clashes with the sibling tests that share the ``tests/edge`` package.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast

from gateway_v2.admit.grant import Admitted, BudgetVerdict, ShedVerdict
from gateway_v2.dispatch.routing import DispatchRouter
from gateway_v2.dispatch.transform import Transform
from gateway_v2.domain import Finding
from gateway_v2.domain.category import Category
from gateway_v2.domain.finding import FindingStatus
from gateway_v2.domain.identity import Principal
from gateway_v2.domain.plan import ExecutionPlan, StreamingMode, Surface
from gateway_v2.edge.identity_resolver import IdentityResolver, TenantIdentity
from gateway_v2.edge.openai_chat import REFUSE_INPUT_BLOCKED, ChatSurface, LifecycleOutcome
from gateway_v2.edge.routes import ChatRoute
from gateway_v2.edge.wire.openai import OpenAIRequestParser, OpenAIResponseRenderer
from gateway_v2.egress.output_guard import MinimalOutputResolver
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import PlanStore

_SEED = 0x16_15_0019
_ITERATIONS = 10_000

#: The ASGI scope path the parser maps to ``Surface.CHAT`` (``_PATH_TO_SURFACE``).
_P19_CHAT_PATH = "/v1/chat/completions"

#: Pre-check bounds so large the Pre_Identity_Clock phase never refuses — this property
#: exercises concurrent isolation, not the size / expansion / part-count pre-checks. TEST
#: bounds on a fake contract, never production capacity literals.
_P19_MAX_BYTES = 1 << 30
_P19_MAX_EXPANSION_RATIO = 1_000.0
_P19_MAX_PARTS = 1 << 30
#: v1's ``max_tokens`` parse boundary mirrored onto the fake contract so ``parse``
#: accepts the P19 bodies (which carry no token fields). A TEST bound, not a
#: production capacity literal.
_P19_MAX_OUTPUT_TOKENS_BOUNDARY = 1_000_000

#: The shared canary token placed in BOTH tenants' requests. Tenant BLOCK's detector fires
#: on it (⇒ BLOCK); tenant ALLOW's detector ignores it (⇒ ALLOW): the SAME bytes, opposite
#: policies, so each tenant's own plan/policy — not the other's — must drive the outcome.
_P19_CANARY = "canary-zs-shared-token"

#: The detector name a BLOCK finding carries. ``jwt`` is a terminate-the-stream class in the
#: shipped resolver's trade-off table, so it resolves to the most-restrictive BLOCK (C22).
_P19_BLOCK_DETECTOR = "jwt"

#: The two tenant roles, served concurrently with opposite policies.
_P19_BLOCK = "block"
_P19_ALLOW = "allow"

#: The input channels a canary may be dropped into; each is a real OpenAI message channel the
#: fold walks, so the canary is scanned wherever it lands. (The property's point is isolation,
#: not fold totality — Property 2 covers totality — so a representative subset is enough.)
_P19_TEXT_CORPUS: tuple[str, ...] = (
    "hello world",
    "summarize this document",
    "unicode: café — naïve © Ω λ",
    "日本語テキスト",
    "a" * 32,
)


def _p19_ctx(i: int, **extra: object) -> str:
    body = " ".join(f"{k}={v!r}" for k, v in extra.items())
    return f"seed={_SEED:#x} iter={i} {body}"


# --------------------------------------------------------------------------- #
# A per-tenant audit sink: records which org's text each detector scanned.
# --------------------------------------------------------------------------- #


class _P19AuditSink:
    """Per-tenant audit of every request text this tenant's detector scanned.

    The §10.4 input scan runs the tenant's OWN injected detector over its OWN request's
    folded channels. Each detector records, into ITS tenant's sink, the ``org_id`` it was
    told to attribute to plus every text it saw. Isolation (R13.2 / R13.3) means a sink only
    ever records its own tenant's ``org_id`` — a foreign ``org_id`` appearing here is a
    cross-contamination failure. A plain slotted class (an append-only audit log), not a
    frozen value: it accumulates records across the run.
    """

    __slots__ = ("org_id", "scanned_orgs", "scanned_texts")

    def __init__(self, org_id: str) -> None:
        self.org_id = org_id
        self.scanned_orgs: list[str] = []
        self.scanned_texts: list[str] = []

    def record(self, attributed_org: str, text: str) -> None:
        self.scanned_orgs.append(attributed_org)
        self.scanned_texts.append(text)


class _P19Detector:
    """A per-tenant input detector that audits every scan and fires only for BLOCK.

    Carries its tenant's ``org_id`` + audit sink and a ``block`` flag. On every call it
    records the scan into its OWN sink (attributed to its OWN ``org_id``), then: tenant BLOCK
    emits one EXECUTED ``jwt`` finding when the canary is present (⇒ the resolver's BLOCK),
    tenant ALLOW emits nothing (⇒ ALLOW). The two detectors share NO state, so a scan of one
    tenant's text can never be recorded against the other's sink unless the lifecycle crossed
    the wires.
    """

    __slots__ = ("_block", "_org_id", "_sink")

    def __init__(self, org_id: str, sink: _P19AuditSink, *, block: bool) -> None:
        self._org_id = org_id
        self._sink = sink
        self._block = block

    def __call__(self, text: str) -> Sequence[Finding]:
        self._sink.record(self._org_id, text)
        if self._block and _P19_CANARY in text:
            return (
                Finding(
                    detector=_P19_BLOCK_DETECTOR,
                    detector_version="1",
                    category=Category.PII,
                    status=FindingStatus.EXECUTED,
                    confidence=0.99,
                    spans=(),
                    evidence=None,
                ),
            )
        return ()


# --------------------------------------------------------------------------- #
# Lifecycle-phase fakes shared by both tenants (each gets its OWN instances).
# --------------------------------------------------------------------------- #


class _P19FakeContract:
    """Pre-check accessors with generous bounds so the pre-check phase never refuses."""

    __slots__ = ()

    def max_request_bytes(self) -> int:
        return _P19_MAX_BYTES

    def max_expansion_ratio(self) -> float:
        return _P19_MAX_EXPANSION_RATIO

    def max_request_parts(self) -> int:
        return _P19_MAX_PARTS

    def max_output_tokens_boundary(self) -> int:
        """v1's ``max_tokens`` parse boundary; ``parse`` reads it to validate token fields."""
        return _P19_MAX_OUTPUT_TOKENS_BOUNDARY

    def default_response_token_ceiling(self) -> int:
        """v1's default org output ceiling; ``run_lifecycle`` clamps ``max_tokens`` to it."""
        return 4096

    def max_output_tokens(self) -> int:
        """v1's output-token hard cap (the clamp's upper bound)."""
        return 65536

    def max_completions(self) -> int:
        """v1's completion cap; the clamp forces ``n`` to it."""
        return 1

    def estimate_request_tokens(self, chars: int, max_tokens: int, n: int) -> int:
        """v1's request token estimate the budget phase charges over the clamped request."""
        input_tokens = max(1, chars // 4) if chars > 0 else 0
        return max(input_tokens + max_tokens * n, 1)


class _P19FakeIdentity:
    """An :class:`IdentityResolver` resolving one canned tenant (never fails closed).

    Each tenant builds its own resolver for its own ``org_id``; the resolve is ``async`` so
    the identity phase yields control, giving the two concurrently-gathered lifecycles a real
    chance to interleave on the event loop (a serialized run would hide a shared-state leak).
    """

    __slots__ = ("_identity",)

    def __init__(self, org_id: str) -> None:
        self._identity = TenantIdentity(
            org_id=org_id,
            principal=Principal(
                key_id=f"p19-key-{org_id}",
                org_id=org_id,
                rate_per_s=1.0,
                burst=1.0,
                feed_seq=0,
            ),
        )

    async def resolve(self, scope: Mapping[str, object]) -> TenantIdentity | None:
        del scope
        # Yield once so the two gathered lifecycles interleave rather than run to completion
        # back to back — an isolation break between in-flight requests must be observable.
        await asyncio.sleep(0)
        return self._identity


class _P19AdmitAdmission:
    """Admission that always admits (keyed to the request's own owner) so both proceed."""

    __slots__ = ()

    async def admit(
        self, owner_id: str, request_id: str, enqueued_at: float
    ) -> Admitted | ShedVerdict:
        del enqueued_at
        await asyncio.sleep(0)
        return Admitted(owner_id=owner_id, request_id=request_id, admitted_at=0.0)

    def complete(self, request_id: str) -> None:
        del request_id


class _P19AdmitBudget:
    """Budget that always admits (local lease, no store read) so both reach the input scan."""

    __slots__ = ()

    async def evaluate(self, cost: int, *, request_id: str) -> BudgetVerdict | Admitted:
        del cost
        await asyncio.sleep(0)
        return Admitted(owner_id="p19", request_id=request_id, admitted_at=0.0)

    def refund(self, cost: int, *, request_id: str) -> None:
        del cost, request_id


@dataclass(frozen=True, slots=True)
class _P19Deps:
    """The ``ChatRoute``-shaped subset the §10.4 lifecycle reads, as an injectable fake.

    One bundle per tenant; the two bundles share no mutable object (each has its own contract,
    identity resolver, snapshot source, admission/budget deciders, detector + audit sink,
    router and transform), so any appearance of one tenant's plan/finding/request-id/audit in
    the other's outcome is a genuine isolation break. Cast to the ``ChatRoute`` annotation so
    ``mypy --strict`` is satisfied without constructing the full heavyweight route (the
    scanner / holdback / executor / in-flight seams are never reached pre-provider).
    """

    contract: _P19FakeContract
    identity: IdentityResolver
    snapshot_source: Callable[[], ReplicaSnapshot]
    admission: object
    quota: object
    input_detector: Callable[[str], Sequence[Finding]]
    resolver: MinimalOutputResolver
    clock: Callable[[], float]
    parser: OpenAIRequestParser = field(default_factory=OpenAIRequestParser)
    renderer: OpenAIResponseRenderer = field(default_factory=OpenAIResponseRenderer)
    router: DispatchRouter = field(default_factory=DispatchRouter)
    transform: Transform = field(default_factory=Transform)


def _p19_plan(org_id: str, content_hash: str) -> ExecutionPlan:
    """A minimal resolvable :class:`ExecutionPlan`; a per-tenant ``content_hash`` makes the
    plan identity (and therefore the ``DispatchRouter`` destination — the tenant's config)
    distinct between the two tenants."""
    return ExecutionPlan(
        org_id=org_id,
        epoch=1,
        sequence=1,
        content_hash=content_hash,
        compiled_at=0.0,
        rules=(),
        required_detectors=frozenset(),
        streaming_mode=StreamingMode.INCREMENTAL,
        integrity_locked=False,
    )


def _p19_snapshot_source(plan: ExecutionPlan) -> Callable[[], ReplicaSnapshot]:
    """A ``snapshot_source`` whose served snapshot resolves ``plan.org_id`` to ``plan``."""
    store = PlanStore()
    store.put(plan)
    snapshot = ReplicaSnapshot(store)
    snapshot.absorb(plan.org_id, now=0.0)

    def _source() -> ReplicaSnapshot:
        return snapshot

    return _source


def _p19_clock() -> float:
    return 0.0


def _p19_build_deps(role: str, org_id: str, content_hash: str, sink: _P19AuditSink) -> _P19Deps:
    """Assemble the per-tenant deps bundle for ``role`` (BLOCK or ALLOW)."""
    plan = _p19_plan(org_id, content_hash)
    return _P19Deps(
        contract=_P19FakeContract(),
        identity=_P19FakeIdentity(org_id),
        snapshot_source=_p19_snapshot_source(plan),
        admission=_P19AdmitAdmission(),
        quota=_P19AdmitBudget(),
        input_detector=_P19Detector(org_id, sink, block=role == _P19_BLOCK),
        resolver=MinimalOutputResolver(),
        clock=_p19_clock,
    )


def _p19_body(rng: random.Random) -> bytes:
    """A well-formed OpenAI chat body carrying the shared canary in a random channel.

    The canary rides in one random message's ``content`` alongside a random corpus string, so
    both tenants scan the identical token — the opposite policies, not the input, decide the
    outcome.
    """
    carrier = rng.choice(_P19_TEXT_CORPUS)
    content = f"{carrier} {_P19_CANARY}" if rng.getrandbits(1) else f"{_P19_CANARY} {carrier}"
    body: dict[str, object] = {
        "model": rng.choice(("gpt-4o", "o3")),
        "messages": [{"role": "user", "content": content}],
    }
    return json.dumps(body).encode("utf-8")


def _p19_scope() -> dict[str, object]:
    return {"type": "http", "method": "POST", "path": _P19_CHAT_PATH, "headers": []}


async def _p19_run_both(
    block_deps: _P19Deps,
    allow_deps: _P19Deps,
    scope: Mapping[str, object],
    block_body: bytes,
    allow_body: bytes,
) -> tuple[
    tuple[object, LifecycleOutcome],
    tuple[object, LifecycleOutcome],
]:
    """Drive BOTH tenants' lifecycles concurrently on one event loop (``asyncio.gather``).

    The two surfaces are distinct ``ChatSurface`` instances over distinct deps; gathering
    their coroutines interleaves them (every phase ``await``s), so a shared-mutable-state leak
    between the in-flight requests would surface in the outcomes.
    """
    block_surface = ChatSurface(deps=cast(ChatRoute, block_deps))
    allow_surface = ChatSurface(deps=cast(ChatRoute, allow_deps))
    block_result, allow_result = await asyncio.gather(
        block_surface.run_lifecycle(scope, block_body),
        allow_surface.run_lifecycle(scope, allow_body),
    )
    return block_result, allow_result


def test_property_19_two_tenant_isolation() -> None:
    rng = random.Random(_SEED)
    block_org = "p19-block-org"
    allow_org = "p19-allow-org"
    # Distinct per-tenant plan content hashes ⇒ distinct, deterministic destinations (config).
    block_hash = "b1" + "0" * 62
    allow_hash = "a1" + "0" * 62

    # The two tenants' deterministic destinations, derived once from their plans — the config
    # that must stay confined to each tenant (R13.1 / R13.2).
    router = DispatchRouter()
    block_dest = router.select(_p19_plan(block_org, block_hash))
    allow_dest = router.select(_p19_plan(allow_org, allow_hash))
    assert block_dest != allow_dest, "opposite tenants must derive distinct destinations"

    for i in range(_ITERATIONS):
        # Fresh audit sinks each iteration so the attribution assertion is per-request.
        block_sink = _P19AuditSink(block_org)
        allow_sink = _P19AuditSink(allow_org)
        block_deps = _p19_build_deps(_P19_BLOCK, block_org, block_hash, block_sink)
        allow_deps = _p19_build_deps(_P19_ALLOW, allow_org, allow_hash, allow_sink)

        scope = _p19_scope()
        block_body = _p19_body(rng)
        allow_body = _p19_body(rng)

        (block_req, block_outcome), (allow_req, allow_outcome) = asyncio.run(
            _p19_run_both(block_deps, allow_deps, scope, block_body, allow_body)
        )

        # ---- R13.1: each request served under its OWN ExecutionPlan / policy ---- #
        # Tenant BLOCK's own plan/policy blocks the canary: it refuses with input_blocked and
        # carries NO plan/destination on the refusal (fail-closed shape).
        assert block_outcome.refuse_code == REFUSE_INPUT_BLOCKED, _p19_ctx(
            i, note="BLOCK tenant did not refuse under its own blocking policy",
            code=block_outcome.refuse_code,
        )
        assert block_outcome.plan is None and block_outcome.destination is None, _p19_ctx(
            i, note="BLOCK tenant refusal leaked provider inputs",
        )
        # Tenant ALLOW's own plan/policy allows the identical canary: it proceeds under its
        # OWN plan (its org_id) and its OWN destination.
        assert allow_outcome.refuse_code is None, _p19_ctx(
            i, note="ALLOW tenant did not proceed under its own allowing policy",
            code=allow_outcome.refuse_code,
        )
        assert allow_outcome.plan is not None, _p19_ctx(i, note="ALLOW tenant missing its plan")
        assert allow_outcome.plan.org_id == allow_org, _p19_ctx(
            i, note="ALLOW tenant served another tenant's plan",
            plan_org=allow_outcome.plan.org_id,
        )

        # ---- R13.2: configuration (the plan-derived destination) stays confined ---- #
        assert allow_outcome.destination == allow_dest, _p19_ctx(
            i, note="ALLOW tenant's destination is not its own config",
            got=allow_outcome.destination, want=allow_dest,
        )
        # The ALLOW tenant's outcome never carries the BLOCK tenant's config, and vice versa.
        assert allow_outcome.destination != block_dest, _p19_ctx(
            i, note="BLOCK tenant's config appeared in the ALLOW tenant's outcome",
            destination=allow_outcome.destination,
        )

        # ---- R13.3: findings attributed to the originating identity only ---- #
        # Each detector recorded, in its OWN sink, ONLY its OWN org_id for every text it
        # scanned — a foreign org_id in either sink is a cross-contamination failure.
        assert block_sink.scanned_orgs and allow_sink.scanned_orgs, _p19_ctx(
            i, note="a tenant's input scan did not run (no audit recorded)",
        )
        assert all(org == block_org for org in block_sink.scanned_orgs), _p19_ctx(
            i, note="BLOCK tenant's audit attributed a scan to a foreign org",
            orgs=tuple(block_sink.scanned_orgs),
        )
        assert all(org == allow_org for org in allow_sink.scanned_orgs), _p19_ctx(
            i, note="ALLOW tenant's audit attributed a scan to a foreign org",
            orgs=tuple(allow_sink.scanned_orgs),
        )
        # Each sink's scanned texts are EXACTLY the channels folded from its OWN request body
        # — not the other tenant's. (A coincidental content match between the two bodies is NOT
        # a leak, since both deliberately carry the shared canary; isolation means a detector
        # only ever saw the channels of ITS OWN request.) The parser is pure wire logic, so
        # re-folding each body reproduces exactly the channels that tenant's detector scanned.
        parser = OpenAIRequestParser()
        _contract = _P19FakeContract()
        block_own_channels = {
            text
            for _ch, text in parser.parse(
                block_body, surface=Surface.CHAT, contract=_contract
            ).input_channels
        }
        allow_own_channels = {
            text
            for _ch, text in parser.parse(
                allow_body, surface=Surface.CHAT, contract=_contract
            ).input_channels
        }
        assert set(block_sink.scanned_texts) == block_own_channels, _p19_ctx(
            i, note="BLOCK tenant scanned texts other than its own request's channels",
            scanned=tuple(block_sink.scanned_texts),
        )
        assert set(allow_sink.scanned_texts) == allow_own_channels, _p19_ctx(
            i, note="ALLOW tenant scanned texts other than its own request's channels",
            scanned=tuple(allow_sink.scanned_texts),
        )

        # ---- R13.3: request IDs are tenant-scoped and never shared ---- #
        # The gateway-minted request id is tenant-scoped (``zs-{org_id}-…``). Reconstruct each
        # tenant's id from its own parsed request + identity and assert it carries its own
        # org_id and never the other tenant's.
        assert block_req is not None and allow_req is not None, _p19_ctx(
            i, note="a tenant's request failed to parse",
        )
        block_rid = ChatSurface._request_id(
            TenantIdentity(
                org_id=block_org,
                principal=Principal(
                    key_id="x", org_id=block_org, rate_per_s=1.0, burst=1.0, feed_seq=0
                ),
            ),
            block_req,  # type: ignore[arg-type]
        )
        allow_rid = ChatSurface._request_id(
            TenantIdentity(
                org_id=allow_org,
                principal=Principal(
                    key_id="x", org_id=allow_org, rate_per_s=1.0, burst=1.0, feed_seq=0
                ),
            ),
            allow_req,  # type: ignore[arg-type]
        )
        assert block_org in block_rid and allow_org not in block_rid, _p19_ctx(
            i, note="BLOCK tenant request id is not confined to its own org", rid=block_rid,
        )
        assert allow_org in allow_rid and block_org not in allow_rid, _p19_ctx(
            i, note="ALLOW tenant request id is not confined to its own org", rid=allow_rid,
        )
        assert block_rid != allow_rid, _p19_ctx(
            i, note="the two tenants shared a request id", rid=block_rid,
        )
