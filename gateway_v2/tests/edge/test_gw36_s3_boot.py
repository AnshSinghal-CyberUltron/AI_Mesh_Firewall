"""GW36 S3 boot selection: `AMF_PROFILE=production` wires real state; nothing else changes."""

from __future__ import annotations

import pytest

from gateway_v2.edge import serve
from gateway_v2.edge.identity_resolver import CachingIdentityResolver, fail_closed_resolver
from gateway_v2.edge.production_profile import ProfileError
from gateway_v2.edge.publisher import Publisher
from gateway_v2.edge.state_runtime import StateConfigError, StateRuntime, StateServedApp
from tests.edge.test_dev_profile_boot import _contract, _dev_env

STATE = {
    "AMF_STATE_VALKEY_URL": "redis://127.0.0.1:1/0",
    "AMF_STATE_HMAC_KEY": "secret",
}


def test_unset_or_empty_profile_keeps_the_fail_closed_table() -> None:
    for env in ({}, {"AMF_PROFILE": ""}, {"AMF_PROFILE": "  "}):
        deps, _logs = serve.build_boot_dependencies(contract=_contract(), env=env)
        assert deps["identity"] is fail_closed_resolver, env
        assert "state_runtime" not in deps


def test_production_wires_signed_state_without_any_io() -> None:
    """Construction must not touch the store: the parent probe builds this and throws it away."""
    env = {"AMF_PROFILE": "production", **STATE}
    deps, logs = serve.build_boot_dependencies(contract=_contract(), env=env)
    runtime = deps["state_runtime"]
    assert isinstance(runtime, StateRuntime)
    assert isinstance(deps["identity"], CachingIdentityResolver)
    assert deps["snapshot_source"] == runtime.snapshot_source
    assert isinstance(deps["metrics_registry"], Publisher) and deps["observer"] is deps[
        "metrics_registry"
    ]
    assert runtime.ready()[0] is False, "nothing is ready before the lifespan ran"
    assert isinstance(serve._assemble_app(deps), StateServedApp)
    joined = "\n".join(logs)
    assert "PRODUCTION PROFILE" in joined and "NOT enforced" in joined
    assert "boot: identity -> fail_closed_resolver" not in joined
    assert "boot: quota -> fail_closed_budget" in joined, "unwired stages stay named"
    assert STATE["AMF_STATE_HMAC_KEY"] not in joined, "the secret never reaches a log line"


@pytest.mark.parametrize("missing", sorted(STATE))
def test_production_refuses_to_start_without_its_state(missing: str) -> None:
    env = {"AMF_PROFILE": "production", **{k: v for k, v in STATE.items() if k != missing}}
    with pytest.raises(StateConfigError, match=missing):
        serve.build_boot_dependencies(contract=_contract(), env=env)


@pytest.mark.parametrize("value", ["prod", "Production", "dev", "1"])
def test_an_unknown_profile_refuses_to_start(value: str) -> None:
    with pytest.raises(ProfileError):
        serve.build_boot_dependencies(contract=_contract(), env={"AMF_PROFILE": value, **STATE})


def test_production_metrics_count_the_401_and_publish_live_state() -> None:
    """E7: `/metrics` keeps serving its fixed series, the 401 lands on `rejected{status=401}`."""
    import asyncio

    from tests.edge.test_gw36_s3_state_identity import _call

    env = {"AMF_PROFILE": "production", **STATE}
    deps, _logs = serve.build_boot_dependencies(contract=_contract(), env=env)
    app = serve._assemble_app(deps)

    async def go() -> tuple[int, str]:
        status, _h, _b = await _call(app, "GET", "/v1/models", auth="Bearer junk")
        assert status == 401
        metrics, _h, body = await _call(app, "GET", "/metrics")
        return metrics, body.decode()

    status, text = asyncio.run(go())
    assert status == 200
    assert 'amf_rejected{status="401",reason="rejected"} 1' in text
    assert "amf_state_identity_held" in text, "the state family is published, read live"


def test_production_and_the_dev_profile_are_exclusive() -> None:
    env = {**_dev_env(), "AMF_PROFILE": "production", **STATE}
    with pytest.raises(ProfileError, match="exclusive"):
        serve.build_boot_dependencies(contract=_contract(), env=env)
    off = {**STATE, "AMF_PROFILE": "production", "AMF_DEV_PROFILE": "0"}
    deps, _logs = serve.build_boot_dependencies(contract=_contract(), env=off)
    assert isinstance(deps["state_runtime"], StateRuntime)
