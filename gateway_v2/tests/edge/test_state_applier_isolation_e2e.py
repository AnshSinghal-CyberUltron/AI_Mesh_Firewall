"""End to end: one hostile record among many good ones, through the gateway's real state runtime.

The control plane is real (`StateWriter` signs into fakeredis, `Rehydrator` stamps) and so is the
gateway (`StateRuntime.cycle`, its gates, its metrics, and requests through the assembled app).
`docs/plans/evidence/2026-11-12-state-applier-isolation/repro_state_applier.py` is the same
scenario as a script, run on develop and on this branch.

On develop each hostile record below either raised out of `cycle()` (stopping the kill-switch,
key and budget kinds too) or refused the plan kind every cycle; once the plan gate aged past the
freshness bound the worker was not ready and every tenant was answered 503.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

import pytest

from gateway_v2.domain.plan import ExecutionPlan, PlanUnavailable, StreamingMode
from gateway_v2.domain.state import StateKind
from gateway_v2.plan.document import PlanDocument, encode_plan_body
from gateway_v2.plan.store import PLAN_QUARANTINED, QUARANTINED, REJECTED
from tests.edge.test_gw36_s3_state_identity import (
    _BODY,
    CHAT,
    Lab,
    _call,
    _console_key,
    _error,
    _sha,
)
from tests.plan.test_lgw05c_delta import _draft

CANARY = "CANARY tenant payload 7f3a"
HOSTILE = "hostile"
GOOD = tuple(f"good-{n}" for n in range(30))


def _body(org: str) -> dict[str, object]:
    return encode_plan_body(PlanDocument(org, StreamingMode.INCREMENTAL, (_draft(),)))


def _deep_regex_body(org: str) -> dict[str, object]:
    body = _body(org)
    rules = body["rules"]
    assert isinstance(rules, list)
    rules.append({
        **rules[0], "rule_id": "deep", "action": "rewrite", "scope": "output",
        "category": "exfil", "rewrite_template": "Removed by policy {rule_id}",
        "condition": {
            "kind": "regex", "field": "", "direction": "output", "keywords": [],
            "pattern": "(" * 499 + CANARY + ")" * 499, "detector_class": "",
            "pipeline_stage": "",
        },
    })
    return body


def _plan_key(lab: Lab, org: str) -> str:
    return lab.runtime._keys.record_key(StateKind.PLAN, org)  # noqa: SLF001 - test seam


def _tamper_signature(lab: Lab) -> None:
    sync = lab.publisher._client  # noqa: SLF001 - the store the writer published into
    raw = sync.get(_plan_key(lab, HOSTILE))
    assert isinstance(raw, bytes)
    sync.set(_plan_key(lab, HOSTILE), raw.replace(b'"sig":"', b'"sig":"0', 1))


def _deep_envelope(lab: Lab) -> None:
    sync = lab.publisher._client  # noqa: SLF001
    sync.set(
        _plan_key(lab, HOSTILE),
        b'{"note":"' + CANARY.encode() + b'","x":' + b"[" * 100_000 + b"]" * 100_000 + b"}",
    )


Scenario = tuple[Callable[[str], dict[str, object]], Callable[[Lab], None] | None, str]
"""(hostile body, store tamper or None, expected held-back state)."""

SCENARIOS: dict[str, Scenario] = {
    "deep_regex_rewrite": (_deep_regex_body, None, REJECTED),
    "signature_mismatch": (_body, _tamper_signature, QUARANTINED),
    "deep_envelope": (_body, _deep_envelope, QUARANTINED),
}


async def _scenario(name: str) -> tuple[Lab, str, dict[str, str]]:
    make_body, tamper, _state = SCENARIOS[name]
    lab = Lab()
    keys = {org: _console_key() for org in (HOSTILE, *GOOD)}
    lab.plan(HOSTILE)
    lab.key(keys[HOSTILE], org=HOSTILE)
    await lab.settle()
    assert lab.runtime.ready() == (True, None)
    for org in GOOD[:15]:
        lab.plan(org)
    lab.writer.plan_set(HOSTILE, make_body(HOSTILE))
    for org in GOOD[15:]:
        lab.plan(org)
    for org in GOOD:
        lab.key(keys[org], org=org)
    lab.writer.budget_set(GOOD[0], 500)
    lab.writer.put(StateKind.KS, f"org:{GOOD[-1]}", {"on": True}, engaged=True)
    if tamper is not None:
        tamper(lab)
    lab.rehydrator.round_once()
    return lab, name, keys


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_one_hostile_plan_record_fails_only_its_own_tenant(
    name: str, caplog: pytest.LogCaptureFixture,
) -> None:
    async def go() -> None:
        lab, _name, keys = await _scenario(name)
        expected = SCENARIOS[name][2]

        with caplog.at_level(logging.DEBUG, logger="amf"):
            reports = await lab.runtime.cycle()

        # 1. The cycle completed: every kind reported ok, so every gate stays fresh.
        assert all(report.ok for report in reports), [(r.kind, r.error) for r in reports]
        assert lab.runtime.ready() == (True, None)
        # 2. Every other tenant's new plan, the KEY, BUDGET and KS deltas were applied.
        for org in GOOD:
            assert isinstance(lab.runtime.snapshot.lookup(org), ExecutionPlan), org
        assert lab.runtime.budget_snapshot.record_for(GOOD[0]) is not None
        assert GOOD[-1] in lab.runtime.switches.view().orgs
        good_status, _h, good_body = await _call(
            lab.app, "POST", CHAT, body=_BODY, auth=f"Bearer {keys[GOOD[3]]}",
        )
        assert _error(good_body)["code"] != "plan_unavailable", good_status
        # 3. The hostile tenant is handled as designed.
        held = lab.runtime.snapshot.held_back(HOSTILE)
        assert held is not None and held.state == expected
        state = lab.runtime.snapshot.lookup(HOSTILE)
        status, _h, body = await _call(
            lab.app, "POST", CHAT, body=_BODY, auth=f"Bearer {keys[HOSTILE]}",
        )
        if expected == QUARANTINED:
            assert isinstance(state, PlanUnavailable) and state.reason == PLAN_QUARANTINED
            assert (status, _error(body)["code"]) == (503, "plan_unavailable")
        else:
            assert isinstance(state, ExecutionPlan), "the previous valid plan keeps serving"
            assert _error(body)["code"] != "plan_unavailable"
        # 4. Metrics and logs.
        series = lab.runtime.metrics.snapshot().series()
        counter = "quarantined" if expected == QUARANTINED else "rejected"
        assert series[f'amf_state_records_{counter}_total{{kind="plan"}}'] == 1
        text = "\n".join(record.getMessage() for record in caplog.records)
        assert CANARY not in text
        assert "maximum recursion" not in text
        assert f"org={HOSTILE!r}" in text and f"error={held.error}" in text
        assert lab.runtime.cycle_failures == 0

    asyncio.run(go())


def test_the_hostile_tenant_stays_handled_after_the_freshness_bound() -> None:
    """The failure mode was time-delayed: everything looked fine until the plan gate aged out."""

    async def go() -> None:
        lab, _name, keys = await _scenario("signature_mismatch")
        await lab.runtime.cycle()
        for _ in range(3):  # three more cycles with fresh stamps: nothing re-fails
            lab.rehydrator.round_once()
            reports = await lab.runtime.cycle()
            assert all(report.ok for report in reports)
        assert lab.runtime.ready() == (True, None)
        series = lab.runtime.metrics.snapshot().series()
        assert series['amf_state_records_quarantined_total{kind="plan"}'] == 1, "counted once"
        status, _h, body = await _call(
            lab.app, "POST", CHAT, body=_BODY, auth=f"Bearer {keys[GOOD[0]]}",
        )
        assert _error(body)["code"] != "plan_unavailable", status

    asyncio.run(go())


def test_one_tampered_key_record_refuses_only_that_key(caplog: pytest.LogCaptureFixture) -> None:
    async def go() -> None:
        lab = Lab()
        lab.plan()
        good, bad = _console_key(), _console_key()
        lab.key(good)
        lab.key(bad)
        await lab.settle()
        for raw in (good, bad):  # both cached as admitted
            assert (await _call(lab.app, "GET", "/v1/models", auth=f"Bearer {raw}"))[0] == 200
        lab.writer.key_add(_sha(bad), "acme", key_id="id-bad", rate_per_s=0.0, burst=0.0)
        lab.writer.key_add(_sha(_console_key()), "acme", key_id="id-new", rate_per_s=0.0, burst=0.0)
        name = lab.runtime._keys.record_hash(StateKind.KEY)  # noqa: SLF001
        sync = lab.publisher._client  # noqa: SLF001
        raw = sync.hget(name, _sha(bad))
        assert isinstance(raw, bytes)
        sync.hset(name, _sha(bad), raw.replace(b'"sig":"', b'"sig":"0', 1))
        lab.rehydrator.round_once()

        with caplog.at_level(logging.DEBUG, logger="amf"):
            reports = await lab.runtime.cycle()

        assert all(report.ok for report in reports)
        assert lab.runtime.ready() == (True, None)
        status, _h, body = await _call(lab.app, "GET", "/v1/models", auth=f"Bearer {bad}")
        assert (status, _error(body)["code"]) == (503, "shared_state_unavailable"), (
            "a key whose record fails verification is refused, never served from cache"
        )
        assert (await _call(lab.app, "GET", "/v1/models", auth=f"Bearer {good}"))[0] == 200
        series = lab.runtime.metrics.snapshot().series()
        assert series['amf_state_records_quarantined_total{kind="key"}'] == 1
        text = "\n".join(record.getMessage() for record in caplog.records)
        assert f"key record quarantined: key={_sha(bad)!r}" in text

    asyncio.run(go())
