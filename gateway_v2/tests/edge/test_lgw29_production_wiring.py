"""GW29 production wiring (AC12): real gate, real control source, ledger fed, boot refusals."""
from __future__ import annotations

import asyncio
import json
import random
import time
from pathlib import Path

import pytest

from gateway_v2.admit.incident_gate import IncidentGate
from gateway_v2.domain.incident import StreamBinding
from gateway_v2.edge import serve
from gateway_v2.edge.incident_passthrough import PassThroughIncidentGate
from gateway_v2.edge.incident_wiring import IncidentAuditTap, IncidentWiring, build_incident
from gateway_v2.edge.inflight_source import (
    DevNoControlSource,
    KillSwitchControlFactory,
    KillSwitchControlSource,
    RefusingControlSource,
)
from gateway_v2.edge.production_profile import ProfileError
from gateway_v2.edge.publisher import Publisher
from gateway_v2.edge.state_runtime import StateServedApp
from gateway_v2.runtime.errors import CapacityUnset
from tests.edge.test_dev_profile_boot import _contract
from tests.edge.test_gw36_s3_boot import STATE
from tests.edge.test_gw36_s3_state_identity import Lab, _console_key, _sha

PROD = {"AMF_PROFILE": "production", **STATE}
CATALOG = {"models": [
    {"name": "gpt-4o", "provider": "openai", "tags": ["pci"], "sensitivity": 2,
     "aliases": ["gpt-4o-2024-08-06"]},
    {"name": "gpt-4o-mini", "provider": "openai", "tags": ["pci"], "sensitivity": 2},
]}


def _boot(env: dict[str, str]) -> tuple[dict[str, object], tuple[str, ...]]:
    return serve.build_boot_dependencies(contract=_contract(), env=env)


def _wiring(deps: dict[str, object]) -> IncidentWiring:
    (wiring,) = deps["lifespan_hooks"]  # type: ignore[misc]
    assert isinstance(wiring, IncidentWiring)
    return wiring


def test_production_binds_the_real_gate_and_control_source_never_a_default() -> None:
    deps, logs = _boot(PROD)
    wiring = _wiring(deps)
    gate = deps["incident_gate"]
    assert isinstance(gate, IncidentGate) and gate is wiring.gate
    assert not isinstance(gate, PassThroughIncidentGate)
    assert type(gate).__name__ != "_FailClosedGate", "never the fail-closed default"
    assert deps["incident_recheck"] == wiring.gate.recheck
    factory = deps["control_factory"]
    assert isinstance(factory, KillSwitchControlFactory)
    source = factory.for_stream(StreamBinding("acme", "gpt-4o", "h" * 64, 1))
    assert isinstance(source, KillSwitchControlSource)
    assert not isinstance(source, DevNoControlSource)
    assert isinstance(factory.for_stream(None), RefusingControlSource)
    assert isinstance(deps["audit"], IncidentAuditTap)
    assert any(line.startswith("boot: kill switch -> enforced at admission and per chunk")
               for line in logs)
    assert not any("NOT enforced on the request path" in line for line in logs)


def test_the_boot_log_names_the_scope_generation_and_catalog_digest(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.write_text(json.dumps(CATALOG), encoding="utf-8")
    deps, logs = _boot({**PROD, "AMF_MODEL_CATALOG": str(path)})
    line = next(item for item in logs if item.startswith("boot: kill switch"))
    wiring = _wiring(deps)
    assert "scope generation 2" in line and wiring.config.digest[:12] in line


def test_the_alias_table_reaches_the_snapshot_parser(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.write_text(json.dumps(CATALOG), encoding="utf-8")
    deps, _ = _boot({**PROD, "AMF_MODEL_CATALOG": str(path)})
    runtime = deps["state_runtime"]
    assert dict(runtime.switches._aliases) == {"gpt-4o-2024-08-06": "gpt-4o"}  # type: ignore[attr-defined]
    assert _wiring(deps).gate._aliases is runtime.switches._aliases  # type: ignore[attr-defined]


@pytest.mark.parametrize("bad", ["models", "{", "[]"])
def test_an_invalid_catalog_refuses_to_start(tmp_path: Path, bad: str) -> None:
    path = tmp_path / "c.json"
    path.write_text(bad, encoding="utf-8")
    with pytest.raises(ProfileError):
        _boot({**PROD, "AMF_MODEL_CATALOG": str(path)})
    with pytest.raises(ProfileError):
        _boot({**PROD, "AMF_MODEL_AUTO_TARGET": "ghost"})


def test_an_underivable_incident_knob_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    from gateway_v2.edge import production_profile

    def unset(*args: object, **kwargs: object) -> object:
        raise CapacityUnset("risk window unset")

    monkeypatch.setattr(production_profile, "build_incident", unset)
    with pytest.raises(ProfileError, match="incident knob"):
        _boot(PROD)


def test_readiness_is_state_first_then_incident_wiring(tmp_path: Path) -> None:
    deps, _ = _boot(PROD)
    ready, reason = deps["readiness"]()  # type: ignore[operator]
    assert ready is False and reason, "state is not ready before the lifespan ran"
    wiring = _wiring(deps)
    assert wiring.ready() == (True, None)


def test_the_pump_and_ticker_run_inside_the_lifespan() -> None:
    deps, _ = _boot(PROD)
    wiring = _wiring(deps)

    class StubRuntime:
        started = stopped = False

        async def startup(self) -> None:
            self.started = True

        async def shutdown(self) -> None:
            self.stopped = True

    async def scenario() -> tuple[bool, bool, bool, bool]:
        stub = StubRuntime()

        async def inner(scope: object, receive: object, send: object) -> None:
            while True:
                message = await receive()  # type: ignore[operator]
                if message["type"] == "lifespan.startup":
                    inner.alive = wiring.pump._task is not None and wiring.ticker._task is not None  # type: ignore[attr-defined]
                if message["type"] == "lifespan.shutdown":
                    return

        inner.alive = False  # type: ignore[attr-defined]
        app = StateServedApp(inner, stub, (wiring,))  # type: ignore[arg-type]
        messages = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])

        async def receive() -> dict[str, str]:
            return next(messages)

        async def send(message: object) -> None:
            return None

        await app({"type": "lifespan"}, receive, send)  # type: ignore[arg-type]
        return (inner.alive, stub.started and stub.stopped,  # type: ignore[attr-defined]
                wiring.pump._task is None, wiring.ticker._task is None)

    alive, runtime_ok, pump_stopped, ticker_stopped = asyncio.run(scenario())
    assert alive and runtime_ok and pump_stopped and ticker_stopped


def test_a_revocation_reaches_the_ledger_through_the_key_delta() -> None:
    lab = Lab()
    wiring = build_incident(
        lab.runtime, _contract(), env={}, clock=time.monotonic, wall=time.time,
        rng=random.Random(1),
    )
    lab.runtime.set_revocation_observer(wiring.ledger.observe)
    raw = _console_key()

    async def scenario() -> tuple[bool, bool]:
        added = lab.writer.key_add(_sha(raw), "acme", key_id="k1", rate_per_s=0.0, burst=0.0)
        await lab.settle()
        revoked_before = wiring.ledger.revoked_since(_sha(raw), added.feed_seq)
        revoke = lab.writer.key_revoke(_sha(raw))
        await lab.settle()
        assert revoke.feed_seq > added.feed_seq
        return revoked_before, wiring.ledger.revoked_since(_sha(raw), added.feed_seq)

    before, after = asyncio.run(scenario())
    assert before is False, "nothing revoked yet"
    assert after is True, "a stream admitted before the revoke is cut"


def test_record_attempt_feeds_the_tracker_and_never_raises() -> None:
    deps, _ = _boot(PROD)
    wiring = _wiring(deps)
    wiring.record_attempt(org_id="o", model="gpt-4o", outcome=None)
    wiring.record_attempt(org_id="o", model="unknown", outcome=None)
    from gateway_v2.domain.incident import ModelOutcome
    wiring.record_attempt(org_id="o", model="unknown", outcome=ModelOutcome.SERVER_ERROR)
    assert wiring.risk.untracked_total == 1, "an un-catalogued model is counted, not tracked"
    wiring.refresh_gauges()
    assert wiring.metrics.series()["amf_incident_scope_generation"] == 2.0
    assert wiring.metrics.series()["amf_incident_killswitch_age_seconds"] == -1.0


def test_the_publisher_declares_the_incident_series() -> None:
    deps, _ = _boot(PROD)
    publisher = deps["metrics_registry"]
    assert isinstance(publisher, Publisher)
    body = publisher.exposition()
    assert "amf_incident_refused_stale_total 0" in body and "amf_incident_risk_score" in body
