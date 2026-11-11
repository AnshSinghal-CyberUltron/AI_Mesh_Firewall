"""DEV PROFILE boot selection, refuse-to-start, admission release, budget lease, and the
default-path regression (``AMF_DEV_PROFILE`` unset ⇒ the shipped fail-closed table, unchanged).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from gateway_v2.admit.admission import AdmissionController
from gateway_v2.admit.grant import Admitted, BudgetVerdict
from gateway_v2.dispatch.provider import StubProviderClient
from gateway_v2.edge import serve
from gateway_v2.edge.app import empty_catalog_source
from gateway_v2.edge.chat_deps import (
    fail_closed_admission,
    fail_closed_budget,
    no_findings_detector,
    unresolved_snapshot_source,
)
from gateway_v2.edge.dev_admit import (
    DevLeaseBudget,
    DevServedApp,
    DevUnlimitedBudget,
    LeaseSettings,
)
from gateway_v2.edge.dev_components import DevProfileError, DevStaticIdentity, EchoProviderClient
from gateway_v2.edge.dev_profile import dev_profile_active, load_dev_config, wrap_dev_app
from gateway_v2.edge.identity_resolver import fail_closed_resolver
from gateway_v2.egress.output_guard import MinimalOutputResolver
from gateway_v2.resolve import PlanResolver
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

EXAMPLE_PLAN = Path(__file__).resolve().parents[2] / "dev" / "plan.example.json"

#: The shipped default boot log, byte-for-byte (the B3 Fail_Closed_Fallback table).
_SHIPPED_DEFAULT_LOGS = (
    "boot: provider -> StubProviderClient (empty, socket-free; real socket B4/GW17-18)",
    "boot: scanner -> detect.holdback.scan (shipped default scan protocol)",
    "boot: resolver -> MinimalOutputResolver (shipped output resolver)",
    "boot: metrics_registry -> minimal exposition registry (GW14d Publisher not wired)",
    "boot: input_detector -> no_findings_detector (fail-closed; identity/admission refuse)",
    "boot: identity -> fail_closed_resolver (every request refuses with declared auth code)",
    "boot: snapshot_source -> unresolved_snapshot_source (unresolved plan refuses)",
    "boot: admission -> fail_closed_admission (fail-closed shed)",
    "boot: quota -> fail_closed_budget (fail-closed budget refusal; no GW06 decider wired)",
    "boot: catalog -> empty_catalog_source (every retrieve model_not_found; backend deferred)",
)
_SHIPPED_DEFAULT_KEYS = frozenset(
    {
        "contract",
        "provider",
        "scanner",
        "resolver",
        "metrics_registry",
        "clock",
        "input_detector",
        "identity",
        "snapshot_source",
        "admission",
        "quota",
        "catalog",
    }
)


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


def _dev_env(**overrides: str) -> dict[str, str]:
    env = {
        "AMF_DEV_PROFILE": "1",
        "AMF_DEV_API_KEY": "sk-dev",
        "AMF_DEV_PLAN_PATH": str(EXAMPLE_PLAN),
        "AMF_DEV_MODELS": "echo-1",
        "AMF_DEV_Q_SAFE": "500",
        "AMF_DEV_AUDIT_DRAIN_RATE_PER_S": "1000",
        "AMF_DEV_AUDIT_BYTES_PER_RECORD": "2765",
        "AMF_DEV_BUDGET": "unlimited",
    }
    env.update(overrides)
    return {key: value for key, value in env.items() if value != "<unset>"}


# --- default path regression ----------------------------------------------------------------- #


@pytest.mark.parametrize("env", [{}, {"AMF_DEV_PROFILE": "0"}, {"AMF_DEV_PROFILE": ""}])
def test_default_boot_is_exactly_the_shipped_fail_closed_table(env: Mapping[str, str]) -> None:
    contract = _contract()
    deps, logs = serve.build_boot_dependencies(contract=contract, env=env)
    assert logs == _SHIPPED_DEFAULT_LOGS
    assert frozenset(deps) == _SHIPPED_DEFAULT_KEYS
    assert deps["contract"] is contract
    assert isinstance(deps["provider"], StubProviderClient) and deps["provider"].script == ()
    assert deps["scanner"] is serve.holdback_scan
    assert isinstance(deps["resolver"], MinimalOutputResolver)
    assert deps["input_detector"] is no_findings_detector
    assert deps["identity"] is fail_closed_resolver
    assert deps["snapshot_source"] is unresolved_snapshot_source
    assert deps["admission"] is fail_closed_admission
    assert deps["quota"] is fail_closed_budget
    assert deps["catalog"] is empty_catalog_source


def test_default_boot_reads_os_environ_and_is_unaffected_by_dev_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dev inputs WITHOUT the switch change nothing (the switch is the only selector)."""
    for key, value in _dev_env(AMF_DEV_PROFILE="<unset>").items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("AMF_DEV_PROFILE", raising=False)
    _deps, logs = serve.build_boot_dependencies(contract=_contract())
    assert logs == _SHIPPED_DEFAULT_LOGS


def test_default_assembled_app_is_not_wrapped() -> None:
    deps, _logs = serve.build_boot_dependencies(contract=_contract(), env={})
    app = serve._assemble_app(deps)
    assert not isinstance(app, DevServedApp)
    assert wrap_dev_app(app, deps) is app


@pytest.mark.parametrize("value", ["yes", "true", "2"])
def test_unrecognised_switch_value_refuses(value: str) -> None:
    with pytest.raises(DevProfileError, match="not recognised"):
        dev_profile_active({"AMF_DEV_PROFILE": value})


# --- dev path -------------------------------------------------------------------------------- #


def test_dev_boot_wires_real_components_and_logs_each_loudly() -> None:
    deps, logs = serve.build_boot_dependencies(contract=_contract(), env=_dev_env())
    assert isinstance(deps["identity"], DevStaticIdentity)
    assert isinstance(deps["resolver"], PlanResolver)
    assert isinstance(deps["admission"], AdmissionController)
    assert isinstance(deps["quota"], DevUnlimitedBudget)
    assert isinstance(deps["provider"], EchoProviderClient)
    assert deps["output_detector"] is deps["error_scanner"]
    assert logs[0].startswith("boot: DEV PROFILE ACTIVE")
    for dep in (
        "provider",
        "resolver",
        "input_detector",
        "identity",
        "snapshot_source",
        "admission",
        "quota",
        "catalog",
        "error_scanner",
        "output_detector",
    ):
        line = next(item for item in logs if item.startswith(f"boot: {dep} ->"))
        assert "DEV PROFILE" in line, line
    admission_line = next(item for item in logs if item.startswith("boot: admission ->"))
    assert admission_line.startswith("boot: admission -> AdmissionController (GW19)")
    assert "q_safe=500.0 (DECLARED, not measured — real value comes from the GW20 fleet run)" in (
        admission_line
    )
    # The slot wrapper is gone: the admission line names the ONE GW15 completion path, not the
    # old "slots released when each ASGI request returns because the GW15 lifecycle never calls
    # complete()" wording.
    assert (
        "the GW15 lifecycle completes each admitted slot on exit — one completion path on both "
        "boot paths, no dev-profile slot wrapper" in admission_line
    )
    # The resolver line names the phase-aware factory, not a single "ONE resolver serves both
    # phases" resolver.
    resolver_line = next(item for item in logs if item.startswith("boot: resolver ->"))
    assert "phase-aware PlanResolver factory" in resolver_line
    assert "wired per phase" in resolver_line
    assert "boot: quota -> DevUnlimitedBudget (DEV PROFILE: budget NOT enforced)" in logs
    assert all("sk-dev" not in line for line in logs), "the API key must never be logged"
    # The slot wrapper is gone: the unlimited-budget dev boot assembles an UNWRAPPED app (there is
    # no lease to drive on lifespan). Wrapping now happens ONLY for a ``DevLeaseBudget`` — covered
    # by ``test_served_app_drives_lease_startup_on_lifespan``.
    assert not isinstance(serve._assemble_app(deps), DevServedApp)


@pytest.mark.parametrize(
    "missing",
    [
        "AMF_DEV_API_KEY",
        "AMF_DEV_PLAN_PATH",
        "AMF_DEV_MODELS",
        "AMF_DEV_Q_SAFE",
        "AMF_DEV_AUDIT_DRAIN_RATE_PER_S",
        "AMF_DEV_AUDIT_BYTES_PER_RECORD",
        "AMF_DEV_BUDGET",
    ],
)
def test_missing_required_dev_input_refuses(missing: str) -> None:
    with pytest.raises(DevProfileError, match=missing):
        load_dev_config(_dev_env(**{missing: "<unset>"}))


@pytest.mark.parametrize("q_safe", ["0", "-1", "nan", "inf", "fast"])
def test_invalid_q_safe_refuses(q_safe: str) -> None:
    with pytest.raises(DevProfileError, match="AMF_DEV_Q_SAFE"):
        load_dev_config(_dev_env(AMF_DEV_Q_SAFE=q_safe))


def test_lease_mode_requires_its_store_inputs() -> None:
    with pytest.raises(DevProfileError, match="AMF_DEV_REDIS_URL"):
        load_dev_config(_dev_env(AMF_DEV_BUDGET="lease"))
    with pytest.raises(DevProfileError, match="AMF_DEV_BUDGET="):
        load_dev_config(_dev_env(AMF_DEV_BUDGET="free"))


def test_renamed_pool_requests_refuses_naming_the_token_replacement() -> None:
    """The old ``AMF_DEV_BUDGET_POOL_REQUESTS`` refuses to start, naming its token replacement.

    The dev lease pool is now sized in tokens (Group F): the request-count name is gone and its
    presence is a hard refuse-to-start so an operator's old env never silently charges the wrong
    unit.
    """
    env = _dev_env(
        AMF_DEV_BUDGET="lease",
        AMF_DEV_REDIS_URL="redis://x",
        AMF_DEV_BUDGET_POOL_REQUESTS="3",
        AMF_DEV_RATE_PER_S="1000",
        AMF_DEV_BURST="100",
        AMF_DEV_LEASE_TTL_S="30",
    )
    with pytest.raises(DevProfileError, match="AMF_DEV_BUDGET_POOL_TOKENS"):
        load_dev_config(env)


def test_main_refuses_to_start_on_missing_dev_input(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    contract = _contract()
    monkeypatch.setattr(serve, "load_contract", lambda: (contract, ()))
    for key, value in _dev_env(AMF_DEV_Q_SAFE="<unset>").items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("AMF_DEV_Q_SAFE", raising=False)

    def _tripwire(*_args: object, **_kwargs: object) -> int:
        raise AssertionError("main hosted the server with a missing dev input")

    monkeypatch.setattr(serve, "_host_app", _tripwire)
    assert serve.main([]) == 2
    payload = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert payload["refuse_start"] is True and "AMF_DEV_Q_SAFE" in payload["error"]


# --- admission release ----------------------------------------------------------------------- #
#
# The shipped GW15 lifecycle now completes every admitted slot on exit (``finally``), so the dev
# profile carries NO per-request slot wrapper: ``build_dev_admission`` returns the bare controller
# and ``DevServedApp`` has no admission role. The lifecycle-level slot behaviour — ``3 ×
# request_depth`` sequential requests, no shed, request depth back to 0, driven through the real
# controller and the real ``ChatRoute.handle`` holds — is proved end-to-end by
# ``test_v2_leaks_d_lifecycle.py::test_real_controller_depth_returns_to_zero_under_steady_load``.
# The two tests that lived here (``test_served_app_releases_admission_slots_per_request`` and
# ``test_bare_controller_wedges_without_complete_documenting_the_lifecycle_gap``) encoded the
# removed slot wrapper and the pre-fix "lifecycle never completes" gap; they are deleted rather
# than re-expressed against the defunct seam.


# --- budget lease over a (fake) Redis -------------------------------------------------------- #


def test_lease_budget_admits_until_the_pool_is_spent_then_refuses() -> None:
    fakeredis = pytest.importorskip("fakeredis")
    client = fakeredis.FakeAsyncRedis()
    # Token pool (not a request count): a 30_000-token pool fully drains into the worker's lease
    # at startup (the token chunk is far larger), and ``evaluate`` now charges the REAL token cost
    # straight through. At 10_000 tokens per call the pool admits exactly three spends and the
    # fourth finds the lease exhausted (overdraft only extends a lease that still holds budget),
    # so the admit/refuse pattern is the same [T, T, T, F, F] shape the request-pool test had.
    settings = LeaseSettings(
        redis_url="redis://unused", pool_tokens=30_000, rate_per_s=1000.0, burst=100, ttl_s=30.0
    )
    budget = DevLeaseBudget(
        contract=_contract(), q_safe=500.0, settings=settings, org_id="dev-org", client=client
    )

    async def _run() -> tuple[str, list[object]]:
        line = await budget.startup()
        verdicts = [await budget.evaluate(10_000, request_id=f"r{i}") for i in range(5)]
        return line, verdicts

    line, verdicts = asyncio.run(_run())
    assert "acquire=ok" in line
    admits = [isinstance(v, Admitted) for v in verdicts]
    assert admits == [True, True, True, False, False]
    # Monotone (robust) view of the same outcome: admits come first, then only refusals, and the
    # pool ends spent — no refusal is ever followed by an admit.
    assert admits == sorted(admits, reverse=True), f"non-monotone admit pattern: {admits}"
    assert isinstance(verdicts[-1], BudgetVerdict)


def test_served_app_drives_lease_startup_on_lifespan(capsys: pytest.CaptureFixture[str]) -> None:
    fakeredis = pytest.importorskip("fakeredis")
    settings = LeaseSettings(
        redis_url="redis://unused", pool_tokens=2, rate_per_s=1000.0, burst=10, ttl_s=30.0
    )
    budget = DevLeaseBudget(
        contract=_contract(),
        q_safe=500.0,
        settings=settings,
        org_id="dev-org",
        client=fakeredis.FakeAsyncRedis(),
    )
    messages = iter(({"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}))

    async def _inner(scope: object, receive: object, send: object) -> None:
        assert callable(receive)
        await receive()
        await receive()

    async def _receive() -> Mapping[str, object]:
        return next(messages)

    async def _send(message: Mapping[str, object]) -> None:
        del message

    app = DevServedApp(_inner, budget=budget)
    asyncio.run(app({"type": "lifespan"}, _receive, _send))
    # ``held`` is ``min(token_chunk, pool_tokens)`` = the 2-token pool (the chunk is far larger).
    assert "boot: DEV PROFILE budget lease acquire=ok held=2" in capsys.readouterr().out
