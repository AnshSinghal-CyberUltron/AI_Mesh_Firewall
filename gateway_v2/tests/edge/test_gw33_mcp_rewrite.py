"""GW33 task 9.4: MCP replies treat REWRITE like REDACT in the re-scan set."""
from __future__ import annotations

import re
from collections.abc import Sequence

from gateway_v2.domain import (
    Action,
    Category,
    Finding,
    FindingStatus,
    Phase,
    Span,
    StreamingMode,
)
from gateway_v2.edge.mcp_reply import _RESCANNED, _WITHHOLD_ON_RESCAN, enforce_reply
from gateway_v2.resolve import PlanResolver
from gateway_v2.runtime.resources import McpBounds
from tests.e2e.gw33_support import plan_of, rule


def _ip_detector(text: str) -> Sequence[Finding]:
    return tuple(
        Finding("out_ip_leak", "v", Category.IP_LEAK, FindingStatus.EXECUTED, 1.0,
                (Span(m.start(), m.end()),), None)
        for m in re.finditer(r"10\.\d+\.\d+\.\d+", text)
    )


def _reply(text: str) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": text}]}}


def _enforce(action: Action, text: str):  # type: ignore[no-untyped-def]
    plan = plan_of(StreamingMode.INCREMENTAL, rule("out_ip_leak", Category.IP_LEAK, action,
                                                   "output.ip_leak", template="[internal]"))
    return enforce_reply(_reply(text), bounds=McpBounds(), detector=_ip_detector,
                         resolver=PlanResolver(plan=plan, phase=Phase.OUTPUT))


def test_a_rewrite_reply_is_rewritten_and_rescanned_clean() -> None:
    outcome = _enforce(Action.REWRITE, "host 10.1.2.3 is up")
    assert outcome.code is None
    assert outcome.result == {"content": [{"type": "text", "text": "host [internal] is up"}]}


def test_the_redact_path_is_unchanged() -> None:
    outcome = _enforce(Action.REDACT, "host 10.1.2.3 is up")
    assert outcome.result == {"content": [{"type": "text", "text": "host [REDACTED] is up"}]}


def test_rewrite_belongs_to_both_rescan_sets() -> None:
    from gateway_v2.domain import Disposition

    assert Disposition.REWRITE in _RESCANNED and Disposition.REWRITE in _WITHHOLD_ON_RESCAN
