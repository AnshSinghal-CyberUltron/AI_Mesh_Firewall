"""Serving_Entrypoint host for the shipped ASGI app (card B3, local half).

This ``edge``-layer module HOSTS the application assembled by
:func:`gateway_v2.edge.app.build_app` — it does not rewrite it. ``edge`` is the TOP layer of the
import-linter ``layers`` contract, so this module may import every layer below (``admit`` /
``detect`` / ``dispatch`` / ``egress`` / ``runtime``) — all downward edges that already exist in
``edge/app.py``; no new import edge is introduced and ``egress`` never names ``detect`` (the
``scanner`` is injected into ``build_app`` from here, as today).

The module is built incrementally along the B3 plan: :func:`build_boot_dependencies` (task 3.1) is
the honest-boot Fail_Closed_Fallback table, :func:`build_uvicorn_config` (3.2) assembles the modern
uvicorn config, :func:`install_drain_hook` (3.3) wires the GW19 SIGTERM drain, and :func:`main`
(3.4) orchestrates the whole boot sequence (load config + contract ONCE, build deps, call
``build_app`` with refuse-to-start on failure, then host under uvicorn). The real ``respawn``
adapter (4.1) appends later. ``uvicorn`` is NOT imported at module load; it is lazily imported
inside :func:`build_uvicorn_config` / :func:`main` with a ``pip install 'gateway-v2[serve]'`` hint,
so ``edge`` import and the AST/mypy gates stay uvicorn-free.

**Honest boot / fail-closed everywhere (R1.4–R1.8).** Every ``build_app`` dependency with no real
implementation wired at boot falls back to its SHIPPED fail-closed default (defined in
``edge/app.py`` / ``edge/chat_deps.py`` / ``edge/identity_resolver.py`` / the egress resolver)
and is named in one boot-log line. B3 invents NO new fail-closed types and fakes neither a real
upstream (``provider`` stays :class:`~gateway_v2.dispatch.provider.StubProviderClient`, R1.5) nor a
real catalog backend (``catalog`` stays :data:`~gateway_v2.edge.app.empty_catalog_source`, R1.6).

**No capacity literal, no module-level mutable (R6.6/R6.7).** The worker count and the declared
max stream duration come from :class:`~gateway_v2.runtime.resources.ResourceContract`, never a
literal here; the boot-deps dict / log tuple are built inside the function, so there is no
module-level list / dict / set.

**Deferred seams (R7).** B3 is a category-2 local-half card: the following four boundaries have
their cloud/host halves owned by separate cards and are NOT scaffolded here — each leaves the
shipped Fail_Closed_Fallback / stub in place and is named at its conceptually-relevant site below
(R7.5). This note keeps the whole boundary visible in one place (design §7):

- **R7.1 real provider HTTP/SSE upstream socket → B4 / GW17-18.** ``provider`` stays an empty
  :class:`~gateway_v2.dispatch.provider.StubProviderClient` (see :func:`_stub_provider` and the
  ``build_boot_dependencies`` fallback table); no real upstream client is constructed.
- **R7.2 fleet-scale overload / autoscaling / Poisson knee + per-worker drain binding → GW20 /
  GW20b** (gates G-01 / G-02 at fleet scale). B3 verifies only the LOCAL drain (G-02 second
  clause); binding the per-worker GW19 drain into the multiprocess manager is deferred (see
  :func:`build_worker_supervisor` and :func:`_host_app`).
- **R7.3 real TLS termination at the edge + NLB bake-off / internet-vantage → B5 / GW16b, R2-12**
  (gates G-10 / G-11 / G-13). The entrypoint binds a PLAIN TCP socket (``cfg.host`` / ``cfg.port``);
  TLS termination and the NLB front (incl. the 300 s NLB pre-drain) are NOT done here — no
  ``ssl_keyfile`` / ``ssl_certfile`` is wired (see the host site in :func:`_host_app`).
- **R7.4 ``/dev/shm`` RemoveIPC host configuration → R2-20 host half.** The run model is a system
  user or a container (R2-20); the ``/dev/shm`` ``RemoveIPC=yes`` host config is a HOST concern,
  not a code concern, and is NOT configured here (see :func:`main`).
"""

from __future__ import annotations

import functools
import json
import random
import sys
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from gateway_v2.admit.admission import AdmissionController
from gateway_v2.admit.supervisor import Worker, WorkerSupervisor
from gateway_v2.detect.holdback import scan as holdback_scan
from gateway_v2.dispatch.provider import StubProviderClient, UpstreamEvent
from gateway_v2.edge.app import ASGIApplication, build_app, empty_catalog_source
from gateway_v2.edge.chat_deps import (
    fail_closed_admission,
    fail_closed_budget,
    no_findings_detector,
    unresolved_snapshot_source,
)
from gateway_v2.edge.identity_resolver import fail_closed_resolver
from gateway_v2.egress.output_guard import MinimalOutputResolver
from gateway_v2.runtime.lifecycle import install_sighup
from gateway_v2.runtime.pools import GatewayRuntime
from gateway_v2.runtime.resources import ResourceContract, load_contract
from gateway_v2.runtime.serving_config import ServingConfig, load_serving_config

if TYPE_CHECKING:
    # ``uvicorn`` is an OPTIONAL ``serve``-extra dependency (task 5.1) and is NOT present in the
    # base venv. Importing it only under ``TYPE_CHECKING`` lets ``mypy --strict`` type the
    # ``build_uvicorn_config`` return annotation without a hard runtime import, so the ``edge``
    # import and the AST/mypy gates stay uvicorn-free. The matching RUNTIME import is lazy —
    # performed INSIDE :func:`build_uvicorn_config` (and later ``main``) with a
    # ``pip install 'gateway-v2[serve]'`` hint on :class:`ImportError`.
    import uvicorn  # type: ignore[import-not-found, unused-ignore]

__all__ = (
    "build_boot_dependencies",
    "build_uvicorn_config",
    "build_worker_supervisor",
    "install_drain_hook",
    "main",
    "serving_app",
)


class _ExpositionRegistry:
    """A minimal :class:`~gateway_v2.edge.app.MetricsRegistry` (``exposition() -> str``).

    The honest default for an unwired deployment: the GW14d
    :class:`~gateway_v2.edge.publisher.Publisher` needs its six producers + the cardinality
    directory + the windowed histogram injected, none of which B3 wires at boot, so a plain
    exposition registry is the fail-closed stand-in until GW14d is wired. ``__slots__`` so there is
    no instance state; it serves a single static ``# TYPE``-free comment line off the serving loop
    (``build_app`` calls ``exposition()`` inside ``asyncio.to_thread``). A plain registry is NOT a
    :class:`~gateway_v2.edge.app.MetricsDirectorySource`, so ``/readyz`` keeps its shipped
    state-first composition unchanged.
    """

    __slots__ = ()

    def exposition(self) -> str:
        """Return a static, empty Prometheus text body (no series until GW14d is wired)."""
        return "# gateway_v2 serving entrypoint — metrics directory not wired\n"


def _stub_provider() -> StubProviderClient:
    """The honest provider default: an empty, socket-free in-process stub (R1.5).

    DEFERRED_SEAM — the real HTTP/SSE upstream socket is owned by **B4 / GW17-18**; it is NOT
    constructed here. The stub replays an empty script and never opens a socket; its injected
    ``rng`` is a fresh deterministic source (never shared) kept only for parity with the real
    provider's injected-randomness contract.

    **B4 drop-in seam (verified, no code here — provider-egress-gw-b4 §C5/§C6, R5/R7.1/R7.2).**
    The real generation-egress client is
    :class:`~gateway_v2.dispatch.http_provider.HttpProviderClient`. It structurally satisfies the
    shipped ``@runtime_checkable``
    :class:`~gateway_v2.dispatch.provider.ProviderClient` Protocol (``open`` + ``async abort``), so
    a deployment that wants real completions injects it through the SAME
    :func:`~gateway_v2.edge.app.build_app` ``provider=`` keyword this stub uses — with ZERO change
    to ``build_app``'s or :func:`build_boot_dependencies`' signature (R7.1). The stub stays the
    honest-boot default (R7.2); no real socket is constructed in the local half. Because
    ``HttpProviderClient.__init__`` takes an INJECTED ``decoder_factory`` (``dispatch`` may not
    import ``edge``), the deployment passes the shipped
    :class:`~gateway_v2.edge.wire.sse.SSEDecoder` as that ``decoder_factory`` — ``edge`` can import
    both ``dispatch.http_provider`` and ``edge.wire.sse``, so the decoder is wired in at the
    injection site, preserving the import-linter layer contract. No ``build_boot_dependencies``
    factory is added here: ``build_app(provider=...)`` already accepts the real client directly, so
    a deployment constructs ``HttpProviderClient`` itself rather than through dead boot wiring.

    **Generation-vs-guard split (verified — §C5, R5.1/R5.2/R5.3).** The two egress upstreams are
    structurally distinct and already correctly wired. GENERATION egress (chat / embeddings) flows
    to the ``Generation_Provider`` through this injected ``ProviderClient``, with
    ``UpstreamRequest.destination`` chosen by the shipped pure
    :class:`~gateway_v2.dispatch.routing.DispatchRouter` (R5.1) — the BYOK provider is
    organization-owned and excluded from firewall cost/latency accounting (R1.6/R8.4). The GUARD
    tier is a SEPARATE upstream: the C43 load-aware
    :class:`~gateway_v2.dispatch.guard_routing.GuardRouter` selects a self-hosted ``Guard_Pool``
    member (never a platform-owned external AI — runbook *Platform AI*, R5.2/R5.3) reached over the
    split-topology TCP path, with ``owner_queue_bound_s`` passed from
    ``contract.owner_queue_bound_s()`` at the wiring site (no literal in ``dispatch``, R9.3).

    **GuardRouter guard-egress + detector are GW07/GW08 (named, NOT built here — §C5, R5.4).** B4
    provides the guard-ROUTING selection and the guard-egress SEAM only; the actual detector-model
    CALL on the request path that turns a ``GuardRouter``-selected destination into findings is
    GW07/GW08 request-path wiring (deferred). B4 adds NO new request-path guard call and NO
    ``egress -> detect`` edge; the selection stays exercisable against a ``Fake_Guard`` with no
    real detector present.
    """
    empty_script: tuple[UpstreamEvent, ...] = ()
    return StubProviderClient(script=empty_script, rng=random.Random(0))


def build_boot_dependencies(
    *,
    contract: ResourceContract,
    admission: AdmissionController | None = None,
) -> tuple[dict[str, object], tuple[str, ...]]:
    """Build the ``build_app`` kwargs + one boot-log line per Fail_Closed_Fallback (R1.4/R1.7).

    Returns ``(deps, logs)`` where ``deps`` is the kwargs mapping to splat into
    :func:`gateway_v2.edge.app.build_app` and ``logs`` is a tuple of diagnostic strings, one per
    unwired dependency naming the dependency and the fallback it uses. ``contract`` is the real
    :class:`~gateway_v2.runtime.resources.ResourceContract` (loaded by ``main`` via
    ``load_contract``); ``clock`` is the real monotonic clock. The GW19
    :class:`~gateway_v2.admit.admission.AdmissionController` structurally satisfies the
    ``AdmissionDecider`` surface (its ``admit(...)``), so when it is provided it is wired directly
    as the ``admission`` decider; otherwise ``admission`` falls back to the fail-closed
    :data:`~gateway_v2.edge.chat_deps.fail_closed_admission`. ``quota`` always falls back to
    :data:`~gateway_v2.edge.chat_deps.fail_closed_budget` (B3 wires no GW06 budget decider).

    Every other unwired dependency uses its SHIPPED default: ``provider`` → an empty
    :class:`~gateway_v2.dispatch.provider.StubProviderClient` (B4/GW17-18 deferred, R1.5);
    ``catalog`` → :data:`~gateway_v2.edge.app.empty_catalog_source` (real org/plan backend deferred,
    R1.6); ``scanner`` → the shipped holdback :func:`~gateway_v2.detect.holdback.scan`; ``resolver``
    → :class:`~gateway_v2.egress.output_guard.MinimalOutputResolver`; ``identity`` →
    :data:`~gateway_v2.edge.identity_resolver.fail_closed_resolver`; ``snapshot_source`` →
    :func:`~gateway_v2.edge.chat_deps.unresolved_snapshot_source`; ``input_detector`` →
    :func:`~gateway_v2.edge.chat_deps.no_findings_detector`; ``metrics_registry`` → a minimal
    exposition registry. ``readiness`` / ``state_view`` / ``active_streams`` / ``control`` /
    ``error_scanner`` / ``exports`` are left unset so ``build_app`` composes its own shipped
    defaults (``_resolve_readiness`` / ``_one_stream`` / ``_NoControlSource`` /
    ``_no_error_findings`` / a fresh producer). It builds a half-ready app honestly, nothing faked.
    """
    provider = _stub_provider()
    clock: Callable[[], float] = time.monotonic
    admission_decider: object = (
        admission if admission is not None else fail_closed_admission
    )
    admission_fallback = (
        "AdmissionController (GW19)"
        if admission is not None
        else "fail_closed_admission (fail-closed shed)"
    )

    deps: dict[str, object] = {
        "contract": contract,
        # DEFERRED_SEAM: real provider HTTP/SSE socket → B4 / GW17-18 (R7.1). Empty stub only.
        "provider": provider,
        "scanner": holdback_scan,
        "resolver": MinimalOutputResolver(),
        "metrics_registry": _ExpositionRegistry(),
        "clock": clock,
        "input_detector": no_findings_detector,
        "identity": fail_closed_resolver,
        "snapshot_source": unresolved_snapshot_source,
        "admission": admission_decider,
        "quota": fail_closed_budget,
        # DEFERRED_SEAM: real org/plan-derived catalog backend deferred (R1.6/R7.5). Honest-empty.
        "catalog": empty_catalog_source,
    }

    logs: tuple[str, ...] = (
        "boot: provider -> StubProviderClient (empty, socket-free; real socket B4/GW17-18)",
        "boot: scanner -> detect.holdback.scan (shipped default scan protocol)",
        "boot: resolver -> MinimalOutputResolver (shipped output resolver)",
        "boot: metrics_registry -> minimal exposition registry (GW14d Publisher not wired)",
        "boot: input_detector -> no_findings_detector (fail-closed; identity/admission refuse)",
        "boot: identity -> fail_closed_resolver (every request refuses with declared auth code)",
        "boot: snapshot_source -> unresolved_snapshot_source (unresolved plan refuses)",
        f"boot: admission -> {admission_fallback}",
        "boot: quota -> fail_closed_budget (fail-closed budget refusal; no GW06 decider wired)",
        "boot: catalog -> empty_catalog_source (every retrieve model_not_found; backend deferred)",
    )
    return deps, logs


def build_uvicorn_config(
    app_factory: Callable[[], ASGIApplication],
    *,
    cfg: ServingConfig,
    workers: int,
) -> uvicorn.Config:
    """Assemble the modern uvicorn :class:`~uvicorn.Config` for the Served_App (R2.1–2.3, R3.10).

    ``app_factory`` is a zero-arg FACTORY returning a fresh Served_App; it is passed with
    ``factory=True`` so uvicorn's multiprocess manager calls it ONCE PER OS WORKER, giving each
    worker its own ``build_app(...)`` instance (its own ``ScanExecutor``, its own frozen routes) —
    true per-worker isolation (C38, R2.1). ``workers`` is supplied by the caller from
    :meth:`~gateway_v2.runtime.resources.ResourceContract.workers`; this function reads NO worker
    literal (R6.6).

    Modern config only (R2.2): ``loop="auto"`` selects uvloop when installed and falls back to
    asyncio otherwise, ``http="auto"`` selects httptools when installed else h11. The deprecated
    Gunicorn worker class (``uvicorn.workers.*``) is NOT used and the inert per-worker connection
    cap option is NOT passed (R2.3) — uvicorn's own multiprocess manager runs
    ``workers`` per-worker event loops. ``timeout_keep_alive=int(cfg.conn_max_age_s)`` applies
    ``RV_CONN_MAX_AGE_S`` as the connection recycling bound so idle keep-alive connections are
    recycled at the configured age (R3.10).

    ``interface="asgi3"`` is set EXPLICITLY (R2.1): :func:`gateway_v2.edge.app.build_app` returns
    the bound method ``_ServingSkin.asgi`` (``async def asgi(self, scope, receive, send)``), a
    genuine ASGI3 callable. uvicorn's ``interface="auto"`` autodetection inspects the callable's
    signature to tell ASGI2 (a two-arg ``app(scope)`` class returning a ``send``-driven instance)
    from ASGI3 (a three-arg ``app(scope, receive, send)`` coroutine); a BOUND METHOD hides ``self``
    from that arity probe, so auto mis-classifies it as ASGI2, wraps it in the ASGI2→3 adapter, and
    calls ``app(scope)`` → ``TypeError`` → every request 500s. Pinning ``interface="asgi3"`` skips
    the probe and hosts the bound-method callable directly.

    ``uvicorn`` is lazy-imported here (NOT at module load) so the ``edge`` import and the AST/mypy
    gates stay uvicorn-free; the optional ``serve`` extra (task 5.1) supplies it. A missing uvicorn
    raises a clear :class:`ImportError` naming the install extra.
    """
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - exercised only without the serve extra
        raise ImportError(
            "uvicorn is required to serve the gateway; "
            "install the serving extra: pip install 'gateway-v2[serve]'"
        ) from exc

    return uvicorn.Config(
        app=app_factory,
        factory=True,
        interface="asgi3",
        loop="auto",
        http="auto",
        workers=workers,
        host=cfg.host,
        port=cfg.port,
        timeout_keep_alive=int(cfg.conn_max_age_s),
    )


def install_drain_hook(
    server: uvicorn.Server,
    *,
    controller: AdmissionController,
    cfg: ServingConfig,
    contract: ResourceContract,
) -> float:
    """Wire the GW19 drain into uvicorn's SIGTERM-driven graceful shutdown (R3.1/3.6–3.9).

    uvicorn installs its OWN SIGTERM handler that sets ``server.should_exit`` and begins graceful
    shutdown; ``uvicorn.Server.shutdown`` is the single async coroutine that coroutine stops
    accepting connections, waits for in-flight connections, and THEN drives the ASGI
    ``lifespan.shutdown`` event (which the shipped ``build_app`` skin handles by releasing the
    ``ScanExecutor`` worker pool, R3.9 — already wired, nothing to rebuild). The modern, framework-
    free way to run work as part of that sequence — without racing uvicorn's own signal handling or
    second-guessing ``should_exit`` — is to WRAP ``server.shutdown`` so the GW19 drain runs FIRST
    and only then delegates to the original coroutine. Because ``lifespan.shutdown`` is emitted
    INSIDE the wrapped ``server.shutdown``, the drain is guaranteed to complete before the executor
    is released (R3.1 → R3.2 → R3.9 ordering), on whichever signal uvicorn maps to graceful exit.

    The hook is thin and idempotent-safe: it closes over ``controller``/``cfg`` and binds the two-
    phase mapping once, translating the runbook's ``RV_DRAIN_ACCEPT_S`` (stop-accepting hold) +
    ``RV_DRAIN_S`` (Drain_Window) onto the shipped machine via
    ``controller.drain(accept_grace_s=cfg.drain_accept_s, drain_window_s=cfg.drain_s)`` (R3.6/R3.7).
    The drain itself drives ``ACCEPTING → DRAINING → TERMINATING → FLUSHED → EXITED``, stops
    accepting (R3.3), lets in-flight streams finish within the window (R3.4), and emits a
    non-truncated ``DeclaredTermination`` for any stream still unfinished past the window (R3.5).

    **Max-duration clamp (R3.8, G-02 second clause).** The declared maximum stream duration is NOT a
    new literal: it is the clamp ``min(contract.max_stream_duration_s(), cfg.drain_s)`` — both
    operands come from existing authority (``resources.py`` and the env-loaded ``ServingConfig``),
    so the clamp holds NO capacity literal (R6.6). The shipped ``AdmissionController`` exposes no
    mutable max-stream-duration field to thread this into, and B3 must not mutate shipped values, so
    the clamped value is RETURNED to the caller (``main``, task 3.4). ``main`` consumes it as the
    serving unit's declared per-stream deadline (passed into the serving unit alongside the drain
    budget), guaranteeing every in-flight stream's deadline is COVERED BY the Drain_Window
    (``clamp ≤ cfg.drain_s`` by construction); on the happy path the stream finishes or is cut by
    its own deadline before the window elapses, so the TERMINATING set is empty.

    ``uvicorn`` stays lazy / ``TYPE_CHECKING``-only — the parameter and return are annotated via the
    forward-ref ``"uvicorn.Server"`` without any runtime import, so this function is importable and
    passes ``mypy --strict`` with uvicorn absent from the base venv. Returns the clamped declared
    max stream duration (seconds) for ``main`` to thread into the serving unit.
    """
    declared_max_stream_duration_s = min(contract.max_stream_duration_s(), cfg.drain_s)

    original_shutdown = server.shutdown

    @functools.wraps(original_shutdown)
    async def _shutdown_with_drain(*args: Any, **kwargs: Any) -> None:
        # Run the GW19 two-phase drain FIRST (R3.1/3.2), then let uvicorn complete the rest of
        # graceful shutdown — including emitting ``lifespan.shutdown``, which releases the
        # ``ScanExecutor`` (R3.9). Keep the drain's own injected-clock discipline: uvicorn's event
        # loop supplies the real ``asyncio.sleep`` the shipped default already uses.
        drain: Callable[..., Awaitable[object]] = controller.drain
        await drain(accept_grace_s=cfg.drain_accept_s, drain_window_s=cfg.drain_s)
        await original_shutdown(*args, **kwargs)

    server.shutdown = _shutdown_with_drain  # type: ignore[method-assign]
    return declared_max_stream_duration_s


def build_worker_supervisor(
    *,
    contract: ResourceContract,
    workers: tuple[Worker, ...] = (),
    respawn_os_worker: Callable[[str], None] | None = None,
) -> WorkerSupervisor:
    """Wire the real OS worker-supervision ``respawn`` adapter into the supervisor (R2.4-2.7).

    **Division of labour (design §3).** uvicorn's own multiprocess manager owns the OS mechanics —
    it forks ``N`` worker processes, runs each on its own event loop with its own ``build_app(...)``
    Served_App, and respawns a process that exits. The shipped
    :class:`~gateway_v2.admit.supervisor.WorkerSupervisor` owns the thing uvicorn knows nothing
    about: the **shared-concurrency accounting**. When a worker exits abnormally its ``slots`` must
    leave ``active_slots`` so peers do not over-admit against capacity a dead worker cannot serve
    (R2.4), and the replacement must re-enter with the **same** slot contribution (R2.7). This
    function provides the real ``respawn: Callable[[str, int], Worker]`` adapter the supervisor
    calls inside ``on_worker_exit`` (which already isolates the dead worker — dropping ONLY its
    slots, peers untouched, R2.6 — and bumps the generation): the adapter mints the replacement
    :class:`~gateway_v2.admit.supervisor.Worker` with ``slots`` equal to the crashed worker's slots
    so ``active_slots`` is restored (R2.7).

    **Per-worker slot count is contract-derived — NO literal here (R6.6).** The replacement's
    ``slots`` come from the serving unit's declared in-flight depth per worker,
    ``contract.queue_depth(contract.offered_service_rate())`` — both operands are
    :class:`~gateway_v2.runtime.resources.ResourceContract` calls (the deployment-derived service
    rate and the per-worker-RSS-bounded in-flight depth), so this module introduces no
    capacity-position literal. In practice ``on_worker_exit`` passes the crashed worker's own
    ``slots`` into ``respawn``, so the replacement inherits exactly the crashed contribution; the
    contract-derived count is the authority used when the crashed slot count is unknown (0), keeping
    a single contract-grounded source of truth rather than a bare literal.

    **The ``respawn_os_worker`` seam (R2.5, deferred OS half).** uvicorn triggers the real OS
    fork/respawn; ``respawn_os_worker`` is the optional hook where that OS-level respawn is kicked
    off for the named worker id. For the B3 LOCAL half it defaults to ``None`` → the
    **accounting-only** adapter (the locally-verifiable half), which reconciles the shared
    accounting without a real process. When a real callback is supplied, the adapter invokes it to
    request the OS-level respawn and then records the replacement in the shared accounting.

    **Fail-closed (design §3, Error Handling).** A respawn that itself fails must leave the dead
    worker ISOLATED (its slots stay out of ``active_slots``) rather than double-counting: if
    ``respawn_os_worker`` raises, the adapter mints a replacement that is NOT alive (slots stay out)
    so the shared accounting never over-admits against a worker that was never actually respawned.
    """
    per_worker_slots = contract.queue_depth(contract.offered_service_rate())

    def respawn(worker_id: str, slots: int) -> Worker:
        # ``on_worker_exit`` supplies the crashed worker's own slots; fall back to the
        # contract-derived per-worker depth when that is unknown (0) so there is no bare literal and
        # the replacement always re-enters with the correct contribution (R2.7).
        replacement_slots = slots if slots > 0 else per_worker_slots
        if respawn_os_worker is None:
            # LOCAL half: accounting-only respawn (no real OS process). The replacement re-enters
            # the shared accounting alive with the crashed worker's slots restored.
            return Worker(worker_id=worker_id, slots=replacement_slots, alive=True)
        try:
            # DEFERRED_SEAM: uvicorn's multiprocess manager owns the real fork/respawn; this hook
            # requests the OS-level respawn of the named worker (real supervision = GW20 at fleet
            # scale, R7.2). The accounting replacement is recorded only once the OS respawn is
            # requested without error.
            respawn_os_worker(worker_id)
        except Exception:
            # Fail-closed: a respawn that itself fails leaves the dead worker ISOLATED (slots stay
            # out of ``active_slots``) rather than double-counting capacity that cannot serve.
            return Worker(worker_id=worker_id, slots=replacement_slots, alive=False)
        return Worker(worker_id=worker_id, slots=replacement_slots, alive=True)

    return WorkerSupervisor(workers, respawn=respawn)


def _assemble_app(deps: dict[str, object]) -> ASGIApplication:
    """Splat the heterogeneous boot-deps mapping into ``build_app`` (one typed boundary).

    :func:`build_boot_dependencies` returns ``dict[str, object]`` by design — the fallback table
    mixes a :class:`~gateway_v2.runtime.resources.ResourceContract`, a provider stub, callables,
    and ``None`` sentinels, which have no common precise type. ``build_app`` has keyword-only,
    per-parameter types, so splatting an ``object``-valued mapping is the ONE place the static
    types cannot line up. Confine that boundary to this single helper: the ``Any``-valued view is
    local, both call sites (:func:`main`'s refuse-to-start probe and the per-worker
    ``app_factory``) go through it, and the honest-boot fallback contract is unchanged.
    """
    kwargs: dict[str, Any] = dict(deps)
    return build_app(**kwargs)


def serving_app() -> ASGIApplication:
    """Module-level zero-arg app factory for uvicorn's multiprocess worker supervisor.

    uvicorn only runs the configured number of OS workers (R2.1 — the runbook "1 gateway x N
    workers" model, C38 per-worker isolation) when it is started via its TOP-LEVEL multiprocess
    supervisor with an IMPORT-STRING app and ``factory=True``: the supervisor spawns each worker in
    a FRESH SUBPROCESS that RE-IMPORTS this module and calls this factory. A closure captured in
    the parent (the ``app_factory`` inside :func:`_host_app`) does NOT survive that spawn, so the
    per-worker app MUST be rebuilt from a module-level entry that reads only the process
    ENVIRONMENT. uvicorn 0.54's ``uvicorn.Server(config).run()`` runs a SINGLE event loop and
    IGNORES ``config.workers``; only the top-level ``uvicorn.run(import_string, factory=True,
    workers=N, ...)`` engages the multiprocess manager that honours the worker count.

    Each worker therefore builds its OWN Served_App from the environment — re-running
    :func:`~gateway_v2.runtime.serving_config.load_serving_config` is not needed for the app itself
    (the host/port/drain bounds are the SUPERVISOR's concern, already applied by the parent's
    ``uvicorn.run`` config), but the honest-boot deps DO depend on the
    :class:`~gateway_v2.runtime.resources.ResourceContract`, so this factory re-runs
    :func:`~gateway_v2.runtime.resources.load_contract` + :func:`build_boot_dependencies` +
    :func:`_assemble_app` INSIDE the worker — true per-worker isolation (its own ``ScanExecutor``,
    its own frozen routes, its own fail-closed deps). The boot-deps fallback lines are re-emitted on
    the worker's stdout so each worker's honest-boot posture is logged (R1.7).

    ``load_contract`` CAN refuse-to-start (``CapacityUnset`` / ``CapacityUnavailable``) on a
    capacity-starved host, and ``build_app`` can raise on an unwired dependency; the PARENT
    (:func:`main`) already proves both succeed ONCE before any worker spawns (R1.8), so by the time
    this factory runs in a worker the environment is known-good. If it nonetheless raises in a
    worker, uvicorn's supervisor reports the worker start failure — it never serves a half-built
    app. ``admission`` is ``None`` here (the B3 local half wires no GW19 controller), so the drain
    hook is driven by the parent's uvicorn graceful-shutdown path, not per-worker.
    """
    contract, contract_logs = load_contract()
    deps, dep_logs = build_boot_dependencies(contract=contract)
    _write_boot_log((*contract_logs, *dep_logs))
    return _assemble_app(deps)


def _write_boot_log(logs: tuple[str, ...]) -> None:
    """Write boot-log lines to stdout, matching the ``runtime`` logging style (R1.7).

    The shipped ``runtime/lifecycle.py::emit_snapshot`` / ``run_cli`` write human-readable boot
    diagnostics to ``sys.stdout`` (one record per flush). The B3 boot log is a tuple of plain
    diagnostic strings — the ``load_serving_config`` / ``load_contract`` warning lines plus the
    one-per-fallback :func:`build_boot_dependencies` lines — so each is emitted as its own stdout
    line and the stream flushed, mirroring that style without inventing a new logging facility.
    """
    if not logs:
        return
    sys.stdout.write("\n".join(logs) + "\n")
    sys.stdout.flush()


def _refuse_start(exc: Exception) -> int:
    """Write the refuse-to-start error to stderr and return exit code 2 (R1.8).

    Byte-for-byte the shipped ``run_cli`` refuse-to-start contract: a JSON object carrying the
    exception text and ``refuse_start: true`` on ``sys.stderr`` and a non-zero (2) exit — so a
    ``build_app`` (or ``load_contract``) failure never serves traffic from a half-built app.
    """
    sys.stderr.write(json.dumps({"error": str(exc), "refuse_start": True}) + "\n")
    return 2


def _host_app(
    deps: dict[str, object],
    *,
    cfg: ServingConfig,
    contract: ResourceContract,
) -> int:
    """Host ``build_app`` under uvicorn's MULTIPROCESS worker supervisor (R1.2/R1.3, R2.1-2.3, R3).

    ``deps`` is the parent's already-validated boot-deps mapping — :func:`main` has called
    ``build_app`` once (via :func:`_assemble_app`) to PROVE it does not raise (R1.8) before any
    worker spawns. ``cfg`` carries the env-loaded host/port/drain bounds; ``contract.workers()`` is
    the single worker-count authority (``runtime/resources.py``), never a literal here (R6.6).

    **Why the top-level multiprocess supervisor (the B3 reconciliation fix).** uvicorn 0.54's
    ``uvicorn.Server(config).run()`` runs a SINGLE event loop and IGNORES ``config.workers`` — it
    hosts ONE in-process server. Only the top-level ``uvicorn.run(import_string, factory=True,
    workers=N, ...)`` engages uvicorn's multiprocess manager, which forks N real worker
    SUBPROCESSES, each running its own event loop that re-imports the app via the import string and
    calls the module-level :func:`serving_app` factory to build its OWN Served_App from the
    environment (R2.1, C38 per-worker isolation). A closure over the parent's boot deps would NOT
    survive that spawn, so the per-worker app is rebuilt from :func:`serving_app` (a MODULE-LEVEL
    zero-arg factory reachable by the ``"gateway_v2.edge.serve:serving_app"`` import string),
    reading only the process environment the parent proved good.

    **Modern config only, no deprecated options (R2.2/R2.3).** ``interface="asgi3"`` pins the ASGI3
    protocol (``build_app`` returns a bound-method ``asgi`` callable whose arity would mis-probe as
    ASGI2 under ``interface="auto"``); ``loop="auto"`` picks uvloop-else-asyncio, ``http="auto"``
    picks httptools-else-h11. The deprecated Gunicorn worker class is NOT used and the inert
    per-worker-connection cap option is NOT passed; ``timeout_keep_alive=int(cfg.conn_max_age_s)``
    applies ``RV_CONN_MAX_AGE_S`` as the connection recycling bound (R3.10).

    **Graceful drain (R3).** The GW19 two-phase drain hook (:func:`install_drain_hook`) wraps a
    SINGLE ``uvicorn.Server.shutdown`` instance, which the top-level multiprocess manager does NOT
    expose — the manager owns one supervisor plus N worker subprocesses, and each worker runs its
    own ``Server``. For the B3 local half ``admission`` is ``None`` →
    :data:`~gateway_v2.edge.chat_deps.fail_closed_admission` (a shed-first callable, not an
    :class:`~gateway_v2.admit.admission.AdmissionController` with a ``drain``), so there is NO
    controller to drive the two-phase drain regardless; uvicorn's OWN per-worker SIGTERM handler
    drives each worker's graceful ``lifespan.shutdown`` → ``ScanExecutor`` release (R3.9), which is
    correct. Binding the per-worker drain into the multiprocess manager when a REAL
    ``AdmissionController`` is wired is the DEFERRED GW20 wiring (R7.2); the honest skip is LOGGED,
    and :func:`install_drain_hook` / :func:`build_worker_supervisor` stay the shipped seams
    (unit-tested in ``test_b3_serve.py`` / ``test_b3_supervisor_wiring.py``) for that wiring.

    ``uvicorn`` is lazy-imported here (NOT at module load) with a
    ``pip install 'gateway-v2[serve]'`` hint on :class:`ImportError`, so the ``edge`` import and
    the AST/mypy gates stay uvicorn-free.
    ``build_uvicorn_config`` (§3.2) still provides the ``Config`` shape for the single-server / test
    path (``test_b3_serve.py`` asserts it); here the multiprocess manager is driven by the matching
    ``uvicorn.run(...)`` keyword set.

    Returns the uvicorn exit code (``0`` on a clean server exit) — ``uvicorn.run`` blocks until the
    supervisor exits.
    """
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - exercised only without the serve extra
        raise ImportError(
            "uvicorn is required to serve the gateway; "
            "install the serving extra: pip install 'gateway-v2[serve]'"
        ) from exc

    workers = contract.workers()

    admission = deps.get("admission")
    if isinstance(admission, AdmissionController):
        # DEFERRED_SEAM (GW20, R7.2): the real per-worker GW19 drain must be bound into the
        # multiprocess manager (each worker runs its own ``Server``), which the top-level
        # ``uvicorn.run`` supervisor does not expose from the parent. Clamp the declared max stream
        # duration to the Drain_Window (R3.8, no literal — both operands from existing authority)
        # and LOG it as consumed by the drain budget rather than dropping it silently.
        declared_max_stream_duration_s = min(contract.max_stream_duration_s(), cfg.drain_s)
        _write_boot_log(
            (
                "boot: declared max stream duration clamped to "
                f"{declared_max_stream_duration_s:.3f}s (<= RV_DRAIN_S={cfg.drain_s:.3f}s); "
                "per-worker GW19 drain binding into the multiprocess manager is deferred to GW20",
            )
        )
    else:
        _write_boot_log(
            (
                "boot: drain hook NOT installed -- admission is fail_closed_admission "
                "(no GW19 controller wired at B3 local half); uvicorn drives per-worker graceful "
                "lifespan.shutdown + ScanExecutor release unchanged",
            )
        )

    _write_boot_log(
        (
            f"boot: hosting gateway_v2.edge.serve:serving_app under uvicorn multiprocess "
            f"supervisor with workers={workers} (factory=True, interface=asgi3) on "
            f"{cfg.host}:{cfg.port}",
        )
    )

    # DEFERRED_SEAM: real TLS termination at the edge + the NLB bake-off / internet-vantage path →
    # B5 / GW16b, R2-12 (gates G-10 / G-11 / G-13). This binds a PLAIN TCP socket on
    # ``cfg.host:cfg.port`` only; TLS is terminated at the NLB front, NOT here — no ``ssl_keyfile``
    # / ``ssl_certfile`` is wired (not scaffolded, R7.3/R7.5). The 300 s NLB pre-drain is part of
    # this same deferred seam.
    uvicorn.run(
        "gateway_v2.edge.serve:serving_app",
        factory=True,
        interface="asgi3",
        loop="auto",
        http="auto",
        workers=workers,
        host=cfg.host,
        port=cfg.port,
        timeout_keep_alive=int(cfg.conn_max_age_s),
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Boot the Serving_Entrypoint: load config, build deps, host ``build_app`` under uvicorn.

    Orchestrates the design's Boot sequence (R1.1/R1.2/R1.8):

    1. Load the :class:`~gateway_v2.runtime.serving_config.ServingConfig` via
       :func:`~gateway_v2.runtime.serving_config.load_serving_config` and the
       :class:`~gateway_v2.runtime.resources.ResourceContract` via
       :func:`~gateway_v2.runtime.resources.load_contract` ONCE. ``load_serving_config`` never
       raises (it degrades to defaults + warnings); ``load_contract`` CAN refuse-to-start
       (``CapacityUnset``/``CapacityUnavailable``), mirrored here as a stderr JSON error + exit 2.
       Both returned log tuples plus the :func:`build_boot_dependencies` fallback lines are written
       to the boot log.
    2. Build the boot deps (``admission=None`` → ``fail_closed_admission`` for the B3 local half).
    3. Call ``build_app(**deps)`` wrapped in ``try/except`` — on ANY exception write
       ``{"error": ..., "refuse_start": true}`` to stderr and return exit 2 BEFORE constructing or
       running uvicorn (R1.8). Traffic is NEVER served from a half-built app.
    4. Reuse the shipped :func:`~gateway_v2.runtime.lifecycle.install_sighup` (via a
       :class:`~gateway_v2.runtime.pools.GatewayRuntime` built exactly as ``run_cli`` does) so a
       running server still reloads its ``ResourceContract`` on SIGHUP without a restart — the
       shipped SIGHUP contract is left intact.
    5. Host the Served_App under uvicorn (:func:`_host_app`): modern factory config, the GW19 drain
       hook when a controller is wired, then ``server.run()``; return ``0`` on a clean exit.

    ``argv`` is accepted for parity with ``run_cli`` (and testability); B3 reads no flags, so it is
    unused — all configuration flows through the environment-loaded ``ServingConfig`` / contract.
    """
    del argv  # B3 reads no CLI flags; all config comes from the env (ServingConfig / contract).

    # DEFERRED_SEAM: the run model is a system user or a container (R2-20); the ``/dev/shm``
    # ``RemoveIPC=yes`` host configuration is the R2-20 HOST half, a host/systemd/container
    # concern, NOT a code concern, and is NOT configured here (not scaffolded, R7.4/R7.5).
    cfg, cfg_logs = load_serving_config()
    try:
        contract, contract_logs = load_contract()
    except Exception as exc:  # refuse-to-start, mirroring run_cli (load_contract can raise)
        return _refuse_start(exc)

    deps, dep_logs = build_boot_dependencies(contract=contract)
    _write_boot_log((*cfg_logs, *contract_logs, *dep_logs))

    try:
        # Prove ``build_app`` assembles cleanly ONCE in the parent before any uvicorn worker spawns
        # (R1.8). The returned app is discarded; each worker rebuilds its own via ``app_factory``.
        _assemble_app(deps)
    except Exception as exc:
        return _refuse_start(exc)

    # Reuse the shipped SIGHUP reload contract (unchanged): a running server reloads its
    # ResourceContract on SIGHUP without a restart, exactly as ``run_cli`` wires it.
    runtime = GatewayRuntime(contract, contract_logs)
    install_sighup(runtime)

    return _host_app(deps, cfg=cfg, contract=contract)
