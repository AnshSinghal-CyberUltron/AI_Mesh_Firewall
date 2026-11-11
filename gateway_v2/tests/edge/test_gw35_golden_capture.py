"""GW35: refusal and failure responses are byte-identical to the pre-change build.

The golden file was captured from the commit before GW35 (``GW35_WRITE_GOLDEN=1`` writes it).
The only allowed differences are the three additive body keys ``zeroshield``, ``pipeline_trace``
and ``source`` and the ``x-zeroshield-action`` header. Gateway-minted ids are normalised.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

import pytest

from gateway_v2.admit.grant import BudgetVerdict, ShedVerdict
from gateway_v2.domain import posture
from gateway_v2.edge.chat_deps import unresolved_snapshot_source
from gateway_v2.edge.identity_resolver import IdentityOutcome
from tests.edge import _gw35_harness as h

GOLDEN = Path(__file__).parent / "golden" / "gw35_pre_change.json"
_ID = re.compile(r"zs-[0-9a-z-]*[0-9a-f]{24}")
_ADDED_KEYS = ("zeroshield", "pipeline_trace", "source")
_ADDED_HEADERS = ("x-zeroshield-action",)


class _Shed:
    async def admit(self, owner_id: str, request_id: str, enqueued_at: float) -> object:
        del owner_id, enqueued_at
        return ShedVerdict(posture.OVERLOAD_SHED, 1.5, False, request_id)

    def complete(self, request_id: str) -> None:
        del request_id


class _Budget:
    def __init__(self, code: str) -> None:
        self.code = code

    async def evaluate(self, cost: int, *, request_id: str, **kw: object) -> object:
        del cost, kw
        return BudgetVerdict(self.code, 1.5, False, request_id)

    def refund(self, *a: object, **k: object) -> None:
        return None


class _Unverified:
    async def resolve(self, scope: object) -> None:
        del scope
        return None

    async def resolve_outcome(self, scope: object) -> IdentityOutcome:
        del scope
        return IdentityOutcome.unverified()


def _ok() -> list:  # type: ignore[type-arg]
    return h.ok_events()


SCENARIOS = {
    "invalid_key": (_ok, None, {"auth": "Bearer nope"}),
    "missing_key": (_ok, None, {"auth": None}),
    "shared_state_unavailable": (_ok, lambda d: d.update(identity=_Unverified()), {}),
    "plan_unavailable": (_ok, lambda d: d.update(snapshot_source=unresolved_snapshot_source), {}),
    "overload_shed": (_ok, lambda d: d.update(admission=_Shed()), {}),
    "rate_limited": (_ok, lambda d: d.update(quota=_Budget(posture.RATE_LIMITED)), {}),
    "budget_unavailable": (
        _ok, lambda d: d.update(quota=_Budget(posture.BUDGET_UNAVAILABLE)), {},
    ),
    "input_blocked": (_ok, None, {"content": "key AKIAIOSFODNN7EXAMPLE"}),
    "upstream_error": (lambda: h.failed_events("upstream_error"), None, {}),
    "upstream_rate_limited": (lambda: h.failed_events("upstream_rate_limited"), None, {}),
    "stream_identity_refusal": (_ok, None, {"auth": "Bearer nope", "stream": True}),
}


def _normalise(text: str) -> str:
    return _ID.sub("zs-ID", text)


def _capture(name: str) -> dict[str, object]:
    events, mutate, kw = SCENARIOS[name]
    app, records, _ = h.build_app(h.ScriptedProvider(events()), mutate_deps=mutate)
    res = asyncio.run(h.post(app, records, **kw))
    return {
        "status": res.status,
        "headers": {k: _normalise(v) for k, v in sorted(res.headers.items())},
        "body": _normalise(res.body.decode("utf-8")),
    }


def _strip(captured: dict[str, object]) -> dict[str, object]:
    headers = {
        k: v for k, v in captured["headers"].items() if k not in _ADDED_HEADERS  # type: ignore[union-attr]
    }
    body = str(captured["body"])
    try:
        parsed = json.loads(body)
    except ValueError:
        return {**captured, "headers": headers}
    if isinstance(parsed, dict):
        for key in _ADDED_KEYS:
            parsed.pop(key, None)
        body = json.dumps(parsed, sort_keys=True)
    return {**captured, "headers": headers, "body": body}


def _canonical(captured: dict[str, object]) -> dict[str, object]:
    stripped = _strip(captured)
    try:
        stripped["body"] = json.dumps(json.loads(str(stripped["body"])), sort_keys=True)
    except ValueError:
        pass
    return stripped


@pytest.mark.skipif(not os.environ.get("GW35_WRITE_GOLDEN"), reason="golden writer")
def test_write_golden() -> None:
    GOLDEN.write_text(
        json.dumps({n: _capture(n) for n in SCENARIOS}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


@pytest.mark.skipif(bool(os.environ.get("GW35_WRITE_GOLDEN")), reason="writing golden")
@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_response_is_identical_to_the_pre_change_build(name: str) -> None:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))[name]
    assert _canonical(_capture(name)) == _canonical(golden), name
