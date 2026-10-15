"""Dry-run gateway-boot + attach seam for the load harness (B6 / R2-21, task 10.1 helper).

Private helper of :mod:`load_harness.dryrun`: the "boot a target + attach a client" concern, split
out so ``dryrun.py`` stays the orchestration + verification module and both stay well under the
house ≤ 800-line size discipline. This module owns everything that touches the booted gateway:

- the in-process PASSING lifecycle deps (resolving identity, warmed pass-through plan snapshot,
  always-admit admission + budget) that open the fail-closed honest-boot gate so a real stream flows
  for the harness to measure;
- the scripted provider / stub that make a non-empty deterministic SSE stream — and, for the
  configured secret-shaped share, secret-SHAPED bytes — flow through the gateway's REAL egress +
  GW12b bounded-holdback path (Req 6, honest MECHANISM-not-detector limit);
- :func:`build_dry_run_app`, assembling the ASGI app via B1's :func:`gateway_v2.edge.app.build_app`;
- the :class:`~load_harness.driver.StreamClient` seam over httpx (:class:`AsgiStreamClient`) and
  :func:`attach_asgi` (the in-process ``httpx.ASGITransport`` attach, design option (a)).

httpx is the ``conformance`` extra — imported LAZILY inside :func:`attach_asgi`, NEVER at module
load — so ``import load_harness.dryrun`` (which imports this module) succeeds on a tree WITHOUT
httpx; the httpx-typed annotations live under ``TYPE_CHECKING`` only. The one allowed import edge is
DOWNWARD into ``gateway_v2`` (``edge.app`` boot seam, ``dispatch.provider`` scripted stub,
``detect.holdback`` scan default, the plan/identity/admit value types, ``runtime.resources``) —
exactly as ``state_control`` imports ``gateway_v2.domain``; ``gateway_v2`` never imports
``load_harness``, so ``lint-imports`` stays contracts-kept (load_harness is OUTSIDE
``root_packages``). No module-level mutable; the one dataclass-free helper classes are ``__slots__``
plain classes (mutable abort latch) exactly like the shipped ``StubProviderClient``.
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING

from gateway_v2.admit.grant import Admitted, BudgetVerdict, ShedVerdict
from gateway_v2.detect.holdback import scan as holdback_scan
from gateway_v2.dispatch.provider import StubProviderClient, UpstreamEvent, UpstreamRequest
from gateway_v2.domain.identity import Principal
from gateway_v2.domain.plan import ExecutionPlan, StreamingMode
from gateway_v2.edge.app import build_app
from gateway_v2.edge.identity_resolver import TenantIdentity
from gateway_v2.egress.output_guard import MinimalOutputResolver
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import PlanStore
from gateway_v2.runtime.resources import ResourceContract
from load_harness.config import RunSpec
from load_harness.driver import StreamOutcome
from load_harness.request_plan import PlannedRequest
from load_harness.strata.prompts import PromptTag
from load_harness.strata.secret_output import SHAPE_TEMPLATES, SecretShapeClass

if TYPE_CHECKING:
    import httpx

__all__ = (
    "DryRunDependencyMissing",
    "AsgiStreamClient",
    "attach_asgi",
    "build_dry_run_app",
    "build_provider",
)

#: The non-TLS chat path the dry-run POSTs to (Req 15.3) — plain HTTP, never TLS.
_CHAT_PATH = "/v1/chat/completions"

#: The in-process ASGITransport base URL. ASGITransport routes by path, not a real host, so the
#: scheme/host are nominal; it is ``http`` (never ``https``) to keep the non-TLS contract explicit
#: (Req 15.3). Not a capacity position.
_ASGI_BASE_URL = "http://load-harness.invalid"

_DRY_RUN_ORG_ID = "load-harness-dry-run-org"

#: Plain content deltas a non-secret scripted stream carries — several events so the C4 worst-chunk
#: is a real maximum over several inter-chunk gaps. Benign filler bytes (no secret shape).
_PLAIN_DELTAS: tuple[str, ...] = ("Hello", ", ", "load", " ", "harness", "!")


class DryRunDependencyMissing(RuntimeError):
    """Raised when an attach-path dependency (httpx) is absent.

    ``import load_harness.dryrun`` must succeed WITHOUT the ``conformance`` extra, so httpx is
    imported lazily inside :func:`attach_asgi`. When that lazy import fails this error carries the
    ``install gateway-v2[conformance]`` hint, mirroring ``serve.py``'s uvicorn ImportError idiom.
    """


# ---- In-process passing lifecycle deps (open the fail-closed honest-boot gate) ------------------
# ``build_app``'s defaults are fail-CLOSED (identity refuses, admission sheds, plan unresolved), so
# an unwired app refuses every request with ZERO provider calls — no stream to measure. These
# PASSING deps resolve one canned tenant, a pass-through plan, and always admit (the shape the
# shipped GW15 lifecycle fixtures use). They fake no measurement — they only open the gate.


class _ResolvingIdentity:
    """An ``IdentityResolver`` resolving one canned tenant for every request (never fail closed)."""

    __slots__ = ("_identity",)

    def __init__(self, org_id: str = _DRY_RUN_ORG_ID) -> None:
        self._identity = TenantIdentity(
            org_id=org_id,
            principal=Principal(
                key_id="load-harness-key",
                org_id=org_id,
                rate_per_s=1.0,
                burst=1.0,
                feed_seq=0,
            ),
        )

    async def resolve(self, scope: Mapping[str, object]) -> TenantIdentity | None:
        """Resolve the one canned identity regardless of ``scope``."""
        del scope
        return self._identity


class _AdmittingAdmission:
    """An admission decider that always admits — the lifecycle reaches budget + provider."""

    __slots__ = ()

    async def admit(
        self, owner_id: str, request_id: str, enqueued_at: float
    ) -> Admitted | ShedVerdict:
        """Admit every request (the dry-run measures the server path, not shedding)."""
        del enqueued_at
        return Admitted(owner_id=owner_id, request_id=request_id, admitted_at=0.0)


class _AdmittingBudget:
    """A budget decider that always admits — the lifecycle reaches the input scan + provider."""

    __slots__ = ()

    async def evaluate(self, cost: int, *, request_id: str) -> BudgetVerdict | Admitted:
        """Admit every request's budget evaluation (no fabricated refusal)."""
        del cost
        return Admitted(owner_id=_DRY_RUN_ORG_ID, request_id=request_id, admitted_at=0.0)


def _pass_through_plan(org_id: str = _DRY_RUN_ORG_ID) -> ExecutionPlan:
    """A minimal resolvable plan (no rules → pass-through enforcement) so the plan phase passes."""
    return ExecutionPlan(
        org_id=org_id,
        epoch=1,
        sequence=1,
        content_hash="loadharness" + "0" * 54,
        compiled_at=0.0,
        rules=(),
        required_detectors=frozenset(),
        streaming_mode=StreamingMode.INCREMENTAL,
        integrity_locked=False,
    )


def _warm_snapshot_source(org_id: str = _DRY_RUN_ORG_ID) -> ReplicaSnapshot:
    """A served snapshot that resolves ``org_id`` to the pass-through plan (deterministic)."""
    store = PlanStore()
    store.put(_pass_through_plan(org_id))
    snapshot = ReplicaSnapshot(store)
    snapshot.absorb(org_id, now=0.0)
    return snapshot


# ---- Scripted stream (plain + secret-shaped) ----------------------------------------------------


def _plain_script() -> tuple[UpstreamEvent, ...]:
    """A clean content stream: one :class:`UpstreamEvent` per plain delta, the last ``final``."""
    last = len(_PLAIN_DELTAS) - 1
    return tuple(
        UpstreamEvent(text_deltas=(("content", text),), final=(i == last))
        for i, text in enumerate(_PLAIN_DELTAS)
    )


def _secret_shaped_script(shape: SecretShapeClass, template: str) -> tuple[UpstreamEvent, ...]:
    """A content stream whose deltas carry secret-SHAPED bytes for the GW12b holdback path (Req 6).

    Emits the obviously-synthetic shape-matched ``template`` (an AWS-/email-/UUID-shaped placeholder
    from :data:`SHAPE_TEMPLATES`) so the booted gateway re-emits those bytes over its REAL egress +
    bounded-holdback pipeline — driving the holdback MECHANISM (keyed on byte shape), NOT the
    GW07/GW08 detector (the honest limit). A plain lead delta precedes the shaped final event.
    """
    del shape  # reporting detail only; the gateway keys the hold on the emitted bytes
    return (
        UpstreamEvent(text_deltas=(("content", "secret-shaped output follows: "),), final=False),
        UpstreamEvent(text_deltas=(("content", template),), final=True),
    )


class _ScriptedProviderClient:
    """A per-``open`` scripted :class:`~gateway_v2.dispatch.provider.ProviderClient` (no socket).

    ``build_app`` takes ONE provider, but which script a request gets (plain vs secret-shaped) is a
    per-request plan decision a FIXED-script ``StubProviderClient`` cannot vary. For each ``open``
    this client draws one ``rng.random()`` and, with probability ``secret_share``, replays the
    secret-shaped script (shape-matched bytes through the real holdback path, Req 6.1); else the
    plain script. ``abort`` flips a one-way latch exactly like the shipped stub (R8.2). A plain
    slotted class (mutable abort latch) satisfying the ``@runtime_checkable`` protocol
    structurally; ``rng`` is injected so the mix is deterministic under the dry-run seed.
    """

    __slots__ = ("_aborted", "_rng", "_secret_share", "_shape", "_template")

    def __init__(
        self,
        *,
        rng: random.Random,
        secret_share: float,
        shape: SecretShapeClass,
        template: str,
    ) -> None:
        self._rng = rng
        self._secret_share = min(1.0, max(0.0, secret_share))
        self._shape = shape
        self._template = template
        self._aborted = False

    async def open(self, request: UpstreamRequest):  # type: ignore[no-untyped-def]
        """Yield the plain or secret-shaped script for this ``open`` (R8.1), honoring abort."""
        del request
        if self._rng.random() < self._secret_share:
            script = _secret_shaped_script(self._shape, self._template)
        else:
            script = _plain_script()
        for event in script:
            if self._aborted:
                return
            yield event

    async def abort(self) -> None:
        """Stop reading from the (stub) upstream for this request (R8.2); idempotent."""
        self._aborted = True


def build_provider(run_spec: RunSpec, rng: random.Random) -> object:
    """Build the provider the booted gateway dispatches to for this dry-run.

    When the secret-shaped stratum is enabled with a positive share, returns a scripted provider
    flowing secret-shaped bytes at that share through the real holdback path (Req 6.1); otherwise
    the shipped :class:`~gateway_v2.dispatch.provider.StubProviderClient` scripted with the
    non-empty plain stream (so a deterministic stream still flows for C4). ``rng`` is injected for
    determinism.
    """
    strata = run_spec.strata
    if strata.secret_output and strata.secret_output_share > 0.0:
        shape, template = SHAPE_TEMPLATES[0]
        return _ScriptedProviderClient(
            rng=rng, secret_share=strata.secret_output_share, shape=shape, template=template
        )
    return StubProviderClient(script=_plain_script(), rng=rng)


# ---- Boot the gateway (build_app) ---------------------------------------------------------------


class _ExpositionRegistry:
    """A minimal ``MetricsRegistry`` (``exposition() -> str``) — the honest unwired default.

    ``build_app`` only reads ``exposition()`` for ``/metrics`` (off the serving loop); the chat path
    the dry-run drives never touches it, so a constant body suffices. ``__slots__`` — no state.
    """

    __slots__ = ()

    def exposition(self) -> str:
        """Return a static, empty Prometheus text body (the dry-run drives only the chat path)."""
        return "# load_harness dry-run — metrics directory not wired\n"


def build_dry_run_app(contract: ResourceContract, provider: object) -> object:
    """Assemble the ASGI app via :func:`build_app` with the scripted provider + passing deps.

    Boots with the shipped honest-boot fail-closed defaults for the egress/scan side EXCEPT it
    injects (a) the scripted ``provider`` so a non-empty deterministic stream flows (option (a)),
    and (b) the passing lifecycle deps so a request reaches the streaming surface rather than being
    refused. ``contract`` is the one capacity authority (its ``target_p99_ms`` is the ladder pass
    bound). ``provider`` is typed ``object`` because the two concrete providers satisfy the
    ``@runtime_checkable`` ``ProviderClient`` protocol structurally but share no nominal base.
    Returns the ``async def app(scope, receive, send)`` callable.
    """
    return build_app(
        contract=contract,
        provider=provider,  # type: ignore[arg-type]
        # The shipped ``detect.holdback.scan`` satisfies ``ScanProtocol`` STRUCTURALLY; its nominal
        # ``token_index: TokenIndex`` is a concrete subtype of the protocol's
        # ``TokenCounterProtocol`` parameter (read contravariantly), so the free function does not
        # match nominally. ``serve.py`` injects the same ``scan`` through an ``object``-valued
        # boot-deps dict for exactly this reason; the one narrow ignore documents the structural
        # match.
        scanner=holdback_scan,  # type: ignore[arg-type]
        resolver=MinimalOutputResolver(),
        metrics_registry=_ExpositionRegistry(),
        clock=time.monotonic,
        identity=_ResolvingIdentity(),
        snapshot_source=_warm_snapshot_source,
        admission=_AdmittingAdmission(),
        quota=_AdmittingBudget(),
    )


# ---- The driver.StreamClient seam over httpx (variant-agnostic) ---------------------------------


def _chat_body(request: PlannedRequest) -> bytes:
    """The well-formed OpenAI chat body for ``request`` (model + messages + ``stream: true``).

    The §10.4 parser rejects a ``model``-less body (the stock SDK always sends ``model``). The
    body comes from the long-prompt stratum pick when enabled (so a 4–7k-token long prompt is a
    genuinely large request input, Req 4) and a short canned string otherwise; neither carries
    secret-shaped bytes (that is the OUTPUT stratum, scripted on the provider). Returns UTF-8 JSON.
    """
    pick = request.prompt_pick
    content = pick.body if pick is not None else "Say hello politely."
    body: dict[str, object] = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": content}],
        "stream": True,
    }
    return json.dumps(body).encode("utf-8")


class AsgiStreamClient:
    """A :class:`~load_harness.driver.StreamClient` fetching over an httpx async client (Req 1).

    Satisfies the driver's structural ``StreamClient`` protocol: ``fetch(request)`` POSTs the chat
    body to the non-TLS chat path and reads the SSE stream, recording each frame's CLIENT-SIDE
    arrival time as a :class:`~load_harness.c4.ChunkArrival` so the C4 collector reduces the worst
    inter-chunk latency (Req 8.1/8.2). It NEVER raises for an ordinary stream failure — a non-2xx,
    transport error, or malformed read is returned as ``StreamOutcome(errored=True, ...)`` so the C4
    ``+inf`` accounting (Req 8.4) stays on the value path. The ``client`` is an already-built
    ``httpx.AsyncClient`` (ASGITransport OR loopback socket) — the seam is variant-agnostic, so the
    same class serves both design attach variants; httpx is typed under ``TYPE_CHECKING`` only.
    """

    __slots__ = ("_client", "_clock")

    def __init__(self, client: httpx.AsyncClient, *, clock: object = time.perf_counter) -> None:
        self._client = client
        self._clock = clock if callable(clock) else time.perf_counter

    async def fetch(self, request: PlannedRequest) -> StreamOutcome:
        """Fetch one request's SSE stream client-vantage, never raising on an ordinary failure."""
        tag = request.prompt_pick.tag if request.prompt_pick is not None else PromptTag.SHORT
        body = _chat_body(request)
        try:
            return await self._stream(body, tag)
        except Exception:  # noqa: BLE001 - an ordinary stream failure is a value, not a crash
            return StreamOutcome(chunks=(), holdback_ms=0.0, errored=True, tag=tag)

    async def _stream(self, body: bytes, tag: PromptTag) -> StreamOutcome:
        """Open the streaming POST and timestamp each SSE ``data:`` frame's client arrival."""
        from load_harness.c4 import ChunkArrival

        headers = {"content-type": "application/json", "accept": "text/event-stream"}
        chunks: list[ChunkArrival] = []
        index = 0
        async with self._client.stream(
            "POST", _CHAT_PATH, content=body, headers=headers
        ) as response:
            if response.status_code != 200:
                await response.aread()
                return StreamOutcome(chunks=(), holdback_ms=0.0, errored=True, tag=tag)
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                chunks.append(ChunkArrival(index=index, arrival_s=self._clock()))
                index += 1
        # Holdback timing is excluded from C4 (Req 8.3); the stub stream exposes no real hold to
        # subtract, so 0.0 is the honest figure.
        return StreamOutcome(chunks=tuple(chunks), holdback_ms=0.0, errored=(index == 0), tag=tag)


def attach_asgi(app: object) -> httpx.AsyncClient:
    """Build an ``httpx.AsyncClient`` bound to an ``ASGITransport`` over ``app`` (Req 12, 15.3).

    The PRIMARY attach variant (design option (a), in-process, no socket): wraps the booted
    ``build_app(...)`` ASGI callable in an ``httpx.ASGITransport`` over the NON-TLS base URL (Req
    15.3 — ``http``, never ``https``). ``httpx`` is the ``conformance`` extra, imported LAZILY here
    (never at module load) so ``import load_harness.dryrun`` succeeds without it; a missing httpx
    raises :class:`DryRunDependencyMissing` with the ``install gateway-v2[conformance]`` hint,
    mirroring ``serve.py``'s lazy-uvicorn idiom. The real-uvicorn loopback variant (B3's
    ``serve.py``, the fidelity target) uses the SAME :class:`AsgiStreamClient` seam over a client
    bound to a loopback base URL instead of a transport; task 10.2 ``importorskip``s uvicorn for it.
    """
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - exercised only without the conformance extra
        raise DryRunDependencyMissing(
            "httpx is required for the load-harness dry-run (in-process ASGITransport); "
            "install the conformance extra: pip install 'gateway-v2[conformance]'"
        ) from exc
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=transport, base_url=_ASGI_BASE_URL)
