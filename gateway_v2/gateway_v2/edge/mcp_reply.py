"""Scan and enforce one broker reply for the MCP surface (GW17, task 6.2).

Every message in the reply is folded (block texts first), scanned with the shared output
detector and the plan's ``Phase.OUTPUT`` resolver, and enforced per leaf with
``apply_decision``. A redaction is checked by scanning the redacted leaves again; any
remaining REDACT or BLOCK finding withholds the reply. A span that straddles two leaves
(a secret split across two blocks) makes ``apply_decision`` refuse, which also withholds.

A server-initiated request in the reply is scanned and then withheld: the broker has no
channel to relay it. An upstream JSON-RPC ``error`` is scanned, then returned as
``mcp_upstream_error`` with only its numeric code, so no agent or upstream exception text
reaches the client.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from gateway_v2.domain import Finding
from gateway_v2.domain.decision import Decision, Disposition
from gateway_v2.edge.input_enforcement import scan_deltas
from gateway_v2.edge.wire.mcp import Fold, fold_reply, rebuild
from gateway_v2.egress.output_guard import OutputResolver, apply_decision
from gateway_v2.runtime.resources import McpBounds

__all__ = ("ReplyOutcome", "enforce_reply")

_OUTPUT_BLOCKED = "output_blocked"
_SCAN_FAILURE = "scan_failure"
_UPSTREAM_ERROR = "mcp_upstream_error"
_WITHHOLD_ON_RESCAN = frozenset({Disposition.REDACT, Disposition.BLOCK})


@dataclass(frozen=True, slots=True)
class ReplyOutcome:
    """The enforced ``result`` (or ``upstream_code`` for an upstream error), or a refusal."""

    result: object = None
    upstream_code: int | None = None
    code: str | None = None
    decision: Decision | None = None


def enforce_reply(
    envelope: object,
    *,
    bounds: McpBounds,
    detector: Callable[[str], Sequence[Finding]],
    resolver: OutputResolver,
) -> ReplyOutcome:
    """Scan, decide, and enforce one broker reply. Never raises."""
    messages = _messages(envelope)
    if messages is None:
        return ReplyOutcome(code=_UPSTREAM_ERROR)
    fold = fold_reply(messages, bounds)
    if isinstance(fold, str):
        return ReplyOutcome(code=fold)
    scan = scan_deltas(fold.pairs(), join_runs=fold.join_runs, detector=detector, resolver=resolver)
    decision = scan.decision
    if scan.refuse_code is not None or decision is None:
        return ReplyOutcome(code=_SCAN_FAILURE)
    if decision.disposition is Disposition.BLOCK or _has_server_request(messages):
        return ReplyOutcome(code=_OUTPUT_BLOCKED, decision=decision)
    masked = _apply(messages, fold, decision, detector, resolver)
    if masked is None:
        return ReplyOutcome(code=_SCAN_FAILURE, decision=decision)
    return _pick(masked, decision)


def _messages(envelope: object) -> tuple[Mapping[str, object], ...] | None:
    items = envelope if isinstance(envelope, list) else [envelope]
    if not items or not all(isinstance(item, dict) for item in items):
        return None
    return tuple(items)


def _has_server_request(messages: tuple[Mapping[str, object], ...]) -> bool:
    return any("method" in message and "id" in message for message in messages)


def _apply(
    messages: tuple[Mapping[str, object], ...],
    fold: Fold,
    decision: Decision,
    detector: Callable[[str], Sequence[Finding]],
    resolver: OutputResolver,
) -> list[object] | None:
    """The messages with every leaf enforced, or ``None`` to withhold."""
    texts: list[str] = []
    base = 0
    try:
        for leaf in fold.leaves:
            texts.append(apply_decision(leaf.text, decision, base))
            base += len(leaf.text)
        if decision.disposition is Disposition.REDACT:
            leaves = zip(fold.leaves, texts, strict=True)
            pairs = tuple((leaf.channel, text) for leaf, text in leaves)
            again = scan_deltas(
                pairs, join_runs=fold.join_runs, detector=detector, resolver=resolver
            )
            if again.decision is None or again.decision.disposition in _WITHHOLD_ON_RESCAN:
                return None
        rebuilt = rebuild(list(messages), fold, tuple(texts))
    except Exception:  # noqa: BLE001 - a straddling span, overlap, or rebuild error withholds
        return None
    return rebuilt if isinstance(rebuilt, list) else None


def _pick(messages: list[object], decision: Decision) -> ReplyOutcome:
    """The one reply message: its ``result``, or its upstream error code."""
    replies = [m for m in messages if isinstance(m, dict) and ("result" in m or "error" in m)]
    if len(replies) != 1:
        return ReplyOutcome(code=_UPSTREAM_ERROR, decision=decision)
    reply = replies[0]
    if "result" in reply:
        return ReplyOutcome(result=reply["result"], decision=decision)
    error = reply.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    upstream = code if isinstance(code, int) and not isinstance(code, bool) else None
    return ReplyOutcome(code=_UPSTREAM_ERROR, upstream_code=upstream, decision=decision)
