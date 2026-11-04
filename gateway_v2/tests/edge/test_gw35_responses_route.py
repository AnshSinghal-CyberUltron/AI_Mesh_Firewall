"""GW35 D-31: /v1/responses shares the chat lifecycle, so it gets the same failed-credential row."""

from __future__ import annotations

import asyncio
import json

from gateway_v2.audit.record import UNAUTHENTICATED_ORG
from tests.edge import _gw35_harness as h

_BODY = json.dumps({"model": "echo-1", "input": "hello"}).encode()


def _call(auth: str | None):  # type: ignore[no-untyped-def]
    app, records, _ = h.build_app(h.ScriptedProvider(h.ok_events()))
    return asyncio.run(
        h.post(app, records, path="/v1/responses", body=_BODY, auth=auth)
    )


def test_a_failed_credential_on_responses_is_audited_under_the_reserved_org() -> None:
    res = _call("Bearer nope")
    assert res.status == 401
    assert [r.org_id for r in res.records] == [UNAUTHENTICATED_ORG]
    console = res.records[0].detail["console"]
    assert console["pipeline_trace"]["stages"][0]["action"] == "block"  # type: ignore[index]
    assert res.json()["zeroshield"]["action"] == "block"  # type: ignore[index]


def test_the_responses_refusal_console_equals_its_audit_console() -> None:
    res = _call("Bearer nope")
    audited = res.records[0].detail["console"]
    assert audited["pipeline_trace"] == res.json()["pipeline_trace"]  # type: ignore[index]


def test_a_successful_responses_call_still_audits_under_its_own_org() -> None:
    res = _call(f"Bearer {h.KEY}")
    assert res.status == 200
    assert {r.org_id for r in res.records} == {"dev-org"}
