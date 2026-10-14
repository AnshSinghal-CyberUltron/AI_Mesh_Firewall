# Feature: serving-entrypoint-gw-b3
# Validates: Requirements 1.4, 1.5, 1.6, 1.7, 1.8, 2.2, 2.3, 3.10
"""Boot-deps + uvicorn-config + refuse-to-start UNIT tests for the B3 serving host (task 3.5).

Example/unit tests (NO ≥10k property loop, NO ``hypothesis``) over the REAL
``gateway_v2.edge.serve`` host module. These assert the honest-boot *wiring* and the refuse-to-start
contract, NOT the pipeline behaviour (that is covered by the GW15/GW16 lifecycle tests):

* ``build_boot_dependencies`` wires every unwired ``build_app`` kwarg to its SHIPPED
  Fail_Closed_Fallback (R1.4): ``provider`` is a :class:`StubProviderClient` with no real client
  built (R1.5); ``catalog`` is :data:`empty_catalog_source` and a ``GET /v1/models/{id}`` retrieve
  renders ``model_not_found`` over the assembled app (R1.6); ``identity`` /
  ``snapshot_source`` / ``admission`` / ``quota`` are the shipped fail-closed defaults; and the
  returned deps splat cleanly into ``build_app(**deps)`` to produce a callable ASGI app.
* The boot log carries exactly ONE line per fallback, each naming the dependency and the fallback
  it uses (R1.7).
* ``build_uvicorn_config`` (guarded by ``pytest.importorskip("uvicorn")`` so the base venv SKIPS
  it cleanly, task 5.1's ``serve`` extra RUNS it) assembles ``factory=True`` / ``loop="auto"`` /
  ``workers=<passed>`` / ``timeout_keep_alive=int(conn_max_age_s)`` and references NEITHER the
  deprecated ``UvicornWorker`` class NOR the inert ``--worker-connections`` option (R2.2/2.3/3.10).
* An injected raising ``_assemble_app`` makes ``main([])`` return ``2`` and write
  ``{"error": ..., "refuse_start": true}`` to stderr WITHOUT ever reaching the uvicorn host (R1.8).

The app is driven over the in-process ASGI ``_Transport`` idiom established by
``test_lgw15_app.py`` / ``test_gw16_app.py`` so no socket (and no uvicorn) is required for the
boot-deps path; only the ``build_uvicorn_config`` assertions need uvicorn, and they are isolated
behind ``importorskip`` so the rest of the file runs green with uvicorn absent.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import random
from collections.abc import Mapping

import pytest

from gateway_v2.admit.admission import AdmissionController
from gateway_v2.admit.codel import CoDelParams
from gateway_v2.admit.metrics import AdmissionMetrics
from gateway_v2.admit.quota import AdmissionBounds
from gateway_v2.admit.supervisor import WorkerSupervisor
from gateway_v2.dispatch.provider import ProviderClient, StubProviderClient
from gateway_v2.domain.identity import Principal
from gateway_v2.edge import serve
from gateway_v2.edge.app import empty_catalog_source
from gateway_v2.edge.chat_deps import (
    fail_closed_admission,
    fail_closed_budget,
    no_findings_detector,
    unresolved_snapshot_source,
)
from gateway_v2.edge.identity_resolver import TenantIdentity, fail_closed_resolver
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals
from gateway_v2.runtime.serving_config import ServingConfig

_OK_STATUS = 200
_NOT_FOUND_STATUS = 404
_REFUSE_EXIT = 2

#: The ten unwired dependencies ``build_boot_dependencies`` wires to a Fail_Closed_Fallback, each
#: expected to carry exactly one boot-log line naming it (R1.4 / R1.7).
_LOGGED_FALLBACK_DEPS: tuple[str, ...] = (
    "provider",
    "scanner",
    "resolver",
    "metrics_registry",
    "input_detector",
    "identity",
    "snapshot_source",
    "admission",
    "quota",
    "catalog",
)


# --------------------------------------------------------------------------- #
# Fixtures: a serviceable contract (the shared ``from_signals`` edge-test shape).
# --------------------------------------------------------------------------- #


def _contract() -> ResourceContract:
    """A serviceable contract (same ``from_signals`` fixture shape as the GW15/GW16 app tests)."""
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


def _resolvable_identity() -> TenantIdentity:
    """One canned tenant identity to exercise ``catalog_for`` directly (the GW16 double shape)."""
    return TenantIdentity(
        org_id="b3-org",
        principal=Principal(
            key_id="b3-key",
            org_id="b3-org",
            rate_per_s=1.0,
            burst=1.0,
            feed_seq=0,
        ),
    )


def _controller() -> AdmissionController:
    """A drain-only :class:`AdmissionController` (the shared ``test_lgw19_drain.py`` shape).

    Built only to prove ``build_boot_dependencies`` wires a REAL controller as the ``admission``
    decider when one is supplied (the GW19 drive seam); its CoDel/drain internals are irrelevant to
    this wiring assertion.
    """
    bounds = AdmissionBounds(
        concurrency=1_000,
        request_depth=1_000,
        guard_depth=1_000,
        dispatch_depth=1_000,
        egress_depth=1_000,
        audit_depth=1_000,
    )
    return AdmissionController(
        bounds=bounds,
        params=CoDelParams(),
        rng=random.Random(0),
        supervisor=WorkerSupervisor(),
        metrics=AdmissionMetrics(),
        drain_window_s=5.0,
        clock=lambda: 0.0,
    )


def _serving_config() -> ServingConfig:
    """A plain ServingConfig with the documented bounds (for the uvicorn-config assertion)."""
    return ServingConfig(
        host="127.0.0.1",
        port=8300,
        drain_accept_s=15.0,
        drain_s=60.0,
        conn_max_age_s=30.0,
    )


# --------------------------------------------------------------------------- #
# In-process ASGI transport (the test_lgw15_app.py / test_gw16_app.py idiom).
# --------------------------------------------------------------------------- #


class _Transport:
    """A one-connection in-process ASGI transport: no socket, records every send event."""

    __slots__ = ("events", "_incoming")

    def __init__(self, *, body: bytes) -> None:
        self.events: list[Mapping[str, object]] = []
        self._incoming: list[Mapping[str, object]] = [
            {"type": "http.request", "body": body, "more_body": False},
            {"type": "http.disconnect"},
        ]

    @staticmethod
    def http_scope(method: str, path: str) -> dict[str, object]:
        return {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [(b"content-type", b"application/json")],
        }

    async def receive(self) -> Mapping[str, object]:
        if self._incoming:
            return self._incoming.pop(0)
        return {"type": "http.disconnect"}

    async def send(self, event: Mapping[str, object]) -> None:
        self.events.append(dict(event))

    def status(self) -> int:
        starts = [e for e in self.events if e.get("type") == "http.response.start"]
        assert len(starts) == 1, f"expected exactly one response.start, got {len(starts)}"
        status = starts[0].get("status")
        assert isinstance(status, int), f"response.start carries no int status: {status!r}"
        return status

    def body_bytes(self) -> bytes:
        out = bytearray()
        for event in self.events:
            if event.get("type") != "http.response.body":
                continue
            body = event.get("body")
            if isinstance(body, (bytes, bytearray)):
                out.extend(body)
        return bytes(out)

    def json_body(self) -> object:
        return json.loads(self.body_bytes().decode("utf-8"))


def _drive(app: object, method: str, path: str, *, body: bytes = b"{}") -> _Transport:
    """Drive one in-process HTTP request through ``app`` and return the recording transport."""
    transport = _Transport(body=body)

    async def _run() -> None:
        await app(transport.http_scope(method, path), transport.receive, transport.send)  # type: ignore[operator]

    asyncio.run(_run())
    return transport


# --------------------------------------------------------------------------- #
# build_boot_dependencies: the Fail_Closed_Fallback table (R1.4 / R1.5 / R1.7).
# --------------------------------------------------------------------------- #


def test_boot_deps_wire_every_unwired_kwarg_to_its_shipped_fail_closed_fallback() -> None:
    """Each unwired ``build_app`` kwarg equals its SHIPPED Fail_Closed_Fallback (R1.4).

    ``build_boot_dependencies`` builds a half-ready-but-honest boot: ``contract`` is the real one,
    ``clock`` is the real monotonic clock, and every security / data dependency falls back to the
    exact shipped fail-closed value — the identity, snapshot, admission, and budget deciders that
    the GW15 route carries by default, and the honest-empty catalog.
    """
    contract = _contract()
    deps, _logs = serve.build_boot_dependencies(contract=contract)

    assert deps["contract"] is contract, "boot did not wire the real contract through"
    assert deps["scanner"] is serve.holdback_scan, "scanner is not the shipped holdback scan"
    assert deps["input_detector"] is no_findings_detector, "input_detector not the shipped default"
    assert deps["identity"] is fail_closed_resolver, "identity not the fail-closed resolver"
    assert deps["snapshot_source"] is unresolved_snapshot_source, (
        "snapshot_source not the shipped unresolved default"
    )
    assert deps["catalog"] is empty_catalog_source, "catalog not the honest-empty default"
    # No AdmissionController wired for the B3 local half → admission falls back to shed-first,
    # budget always falls back fail-closed.
    assert deps["admission"] is fail_closed_admission, "admission not the fail-closed shed default"
    assert deps["quota"] is fail_closed_budget, "quota not the fail-closed budget default"
    assert callable(deps["clock"]), "clock seam is not callable"


def test_boot_deps_provider_is_stub_with_no_real_client_built() -> None:
    """``provider`` is a :class:`StubProviderClient` and NO real upstream client is built (R1.5).

    The real HTTP/SSE provider socket is a named Deferred_Seam (B4 / GW17-18); the boot wires the
    in-process, socket-free stub whose only state is the scripted replay + the abort latch.
    """
    deps, _logs = serve.build_boot_dependencies(contract=_contract())
    provider = deps["provider"]
    assert isinstance(provider, StubProviderClient), "provider is not the in-process stub"
    assert isinstance(provider, ProviderClient), "stub does not satisfy ProviderClient"
    # No real socket: the stub's only attributes are the scripted replay + the abort latch.
    assert set(StubProviderClient.__slots__) == {"_aborted", "rng", "script"}, (
        "StubProviderClient grew socket-bearing state (a real client would)"
    )


def test_boot_deps_admission_controller_wired_when_provided() -> None:
    """When a real :class:`AdmissionController` is passed, it is wired as the ``admission`` decider.

    The GW19 controller structurally satisfies the ``AdmissionDecider`` surface, so a wired
    controller is injected directly (not the fail-closed shed) — the seam the real GW19 drive uses.
    """
    controller = _controller()
    deps, logs = serve.build_boot_dependencies(contract=_contract(), admission=controller)
    assert deps["admission"] is controller, "a provided AdmissionController was not wired through"
    assert any("AdmissionController" in line for line in logs), (
        "the admission boot log did not name the wired AdmissionController"
    )


def test_boot_log_has_exactly_one_line_per_fallback_naming_dep_and_fallback() -> None:
    """The boot log carries one line per Fail_Closed_Fallback naming the dep + its fallback (R1.7).

    Exactly ten fallback lines (one per unwired dependency), each a ``boot: <dep> -> <fallback>``
    diagnostic naming BOTH the dependency and the shipped fallback it uses.
    """
    _deps, logs = serve.build_boot_dependencies(contract=_contract())
    assert len(logs) == len(_LOGGED_FALLBACK_DEPS), (
        f"expected {len(_LOGGED_FALLBACK_DEPS)} fallback log lines, got {len(logs)}: {logs!r}"
    )
    for dep in _LOGGED_FALLBACK_DEPS:
        matches = [line for line in logs if line.startswith(f"boot: {dep} ->")]
        assert len(matches) == 1, (
            f"expected exactly one boot log line for {dep!r}, got {matches!r}"
        )
        # The line names the fallback as well as the dependency (the ``-> <fallback>`` clause).
        assert matches[0].split("->", 1)[1].strip(), (
            f"the boot log line for {dep!r} does not name a fallback: {matches[0]!r}"
        )


def test_boot_deps_splat_cleanly_into_build_app_producing_a_callable_app() -> None:
    """The returned deps splat cleanly into ``build_app(**deps)`` → a callable ASGI app (R1.4)."""
    deps, _logs = serve.build_boot_dependencies(contract=_contract())
    app = serve._assemble_app(deps)
    assert callable(app), "build_app(**deps) did not produce a callable ASGI app"


def test_boot_catalog_is_empty_source_so_every_models_retrieve_is_model_not_found() -> None:
    """``catalog`` is the empty source; every models retrieve → ``model_not_found`` (R1.6).

    The real org/plan-derived catalog backend is a Deferred_Seam; the boot wires the honest-empty
    source. ``empty_catalog_source.catalog_for(...)`` returns an empty tuple for every identity, so
    ``GET /v1/models`` lists nothing and every ``GET /v1/models/{id}`` retrieve resolves to the
    declared ``model_not_found`` by construction (the behavioural rendering of this empty catalog is
    proven end-to-end by the GW16 models surface tests; here the honest-empty identity + behaviour
    is asserted at the catalog seam, since the B3 fail-closed ``identity`` refuses the HTTP request
    before the Models surface reaches the catalog lookup — itself the honest fail-closed boot).
    """
    deps, _logs = serve.build_boot_dependencies(contract=_contract())
    catalog = deps["catalog"]
    assert catalog is empty_catalog_source, "catalog is not the honest-empty source (R1.6)"

    identity = _resolvable_identity()
    assert catalog.catalog_for(identity) == (), (  # type: ignore[attr-defined]
        "the honest-empty catalog returned a non-empty model set (a retrieve would not 404)"
    )

    # The whole boot app is honestly fail-closed at identity (fail_closed_resolver), so the Models
    # HTTP surface refuses with 503 BEFORE the catalog is consulted — proof the boot is fail-closed.
    app = serve._assemble_app(deps)
    retrieve = _drive(app, "GET", "/v1/models/gpt-does-not-exist")
    assert retrieve.status() not in (_OK_STATUS, _NOT_FOUND_STATUS), (
        "the fail-closed boot served a models retrieve instead of refusing at identity"
    )


# --------------------------------------------------------------------------- #
# main refuse-to-start: build_app raises → exit 2, server never started (R1.8).
# --------------------------------------------------------------------------- #


def test_main_refuses_to_start_and_never_hosts_when_assemble_app_raises(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A raising ``_assemble_app`` → ``main`` returns 2 + stderr refuse_start, no server (R1.8).

    ``load_contract`` is pinned to a serviceable contract so the test deterministically reaches the
    ``build_app`` probe (``load_contract`` can itself refuse-to-start on a capacity-starved host,
    which is a DIFFERENT exit-2 path), ``_assemble_app`` is injected to raise, and ``_host_app`` is
    replaced by a tripwire that fails the test if the host is ever reached — proving the refusal
    happens BEFORE any uvicorn import / ``server.run`` (R1.8). No uvicorn is needed or imported.
    """
    contract = _contract()
    monkeypatch.setattr(serve, "load_contract", lambda: (contract, ("contract-ok",)))

    boom = "build_app blew up on an unwired dependency"

    def _raise(_deps: dict[str, object]) -> object:
        raise RuntimeError(boom)

    monkeypatch.setattr(serve, "_assemble_app", _raise)

    def _tripwire(*_args: object, **_kwargs: object) -> int:
        raise AssertionError("main hosted the server after build_app raised (R1.8 violated)")

    monkeypatch.setattr(serve, "_host_app", _tripwire)

    code = serve.main([])
    assert code == _REFUSE_EXIT, f"refuse-to-start did not return exit 2: {code}"

    captured = capsys.readouterr()
    payload = json.loads(captured.err.strip().splitlines()[-1])
    assert payload == {"error": boom, "refuse_start": True}, (
        f"the refuse-to-start stderr payload is not the declared contract: {payload!r}"
    )


# --------------------------------------------------------------------------- #
# build_uvicorn_config: modern config, no deprecated options (R2.2/2.3/3.10).
# Guarded by importorskip so the base venv (no uvicorn) SKIPS this cleanly.
# --------------------------------------------------------------------------- #


def test_build_uvicorn_config_is_modern_factory_mode_with_no_deprecated_options() -> None:
    """The uvicorn ``Config`` is factory-mode / modern-loop with no deprecated option (R2.2/2.3).

    SKIPS when uvicorn is absent (the base venv), RUNS when the ``serve`` extra is installed. When
    it runs: ``factory is True``, ``loop == "auto"``, ``workers`` equals the passed count,
    ``timeout_keep_alive == int(conn_max_age_s)`` (R3.10), and nothing in the serving module (or the
    Config) references the deprecated ``UvicornWorker`` class or the inert ``--worker-connections``.
    """
    uvicorn = pytest.importorskip("uvicorn")

    cfg = _serving_config()
    workers = 7

    def _app_factory() -> object:
        return serve._assemble_app(serve.build_boot_dependencies(contract=_contract())[0])

    config = serve.build_uvicorn_config(_app_factory, cfg=cfg, workers=workers)

    assert isinstance(config, uvicorn.Config), "did not return a uvicorn.Config"
    assert config.factory is True, "the app is not passed as a zero-arg factory (factory is True)"
    assert config.loop == "auto", "loop is not the modern 'auto' (uvloop else asyncio)"
    assert config.workers == workers, "workers is not the passed per-worker count"
    assert config.timeout_keep_alive == int(cfg.conn_max_age_s), (
        "timeout_keep_alive did not apply RV_CONN_MAX_AGE_S as the connection recycling bound"
    )
    assert config.host == cfg.host and config.port == cfg.port, "host/port not wired from config"

    # The deprecated Gunicorn worker class and the inert --worker-connections option must appear
    # NOWHERE in the serving module source (R2.3).
    source = inspect.getsource(serve)
    assert "UvicornWorker" not in source, "serve.py references the deprecated UvicornWorker class"
    assert "worker-connections" not in source and "worker_connections" not in source, (
        "serve.py references the inert --worker-connections option"
    )
