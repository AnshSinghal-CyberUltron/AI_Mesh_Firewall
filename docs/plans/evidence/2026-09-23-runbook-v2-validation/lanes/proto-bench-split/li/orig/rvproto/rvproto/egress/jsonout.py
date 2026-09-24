"""Non-stream egress: output scan of the full content -> resolve(OUTPUT) -> emit."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

import orjson

from rvproto.domain.decision import Decision, Disposition
from rvproto.domain.ports import OutputInspector
from rvproto.domain.text import redact_text


@dataclass(frozen=True, slots=True)
class JsonOut:
    body: bytes
    decision: Decision
    scanned_texts: int


def _targets(doc: Any) -> list[tuple[tuple[Any, ...], str]]:
    out: list[tuple[tuple[Any, ...], str]] = []
    for ci, choice in enumerate(doc.get("choices") or []):
        msg = choice.get("message") or {}
        if isinstance(msg.get("content"), str):
            out.append((("choices", ci, "message", "content"), msg["content"]))
        for ti, tc in enumerate(msg.get("tool_calls") or []):
            args = (tc.get("function") or {}).get("arguments")
            if isinstance(args, str):
                out.append((("choices", ci, "message", "tool_calls", ti, "function", "arguments"), args))
    return out


def inspect_json(body: bytes, inspector: OutputInspector) -> JsonOut:
    """Raises orjson.JSONDecodeError on a malformed provider body (protocol error)."""
    doc = orjson.loads(body)
    targets = _targets(doc)
    hits = [inspector.hits(text) for _, text in targets]
    decision = inspector.decide(hits)
    if decision.disposition is Disposition.BLOCK or not decision.transformations:
        return JsonOut(body, decision, len(targets))
    by_seg: dict[int, list[tuple[int, int, str]]] = {}
    for t in decision.transformations:
        by_seg.setdefault(t.span.segment, []).append((t.span.start, t.span.end, t.replacement))
    new = copy.deepcopy(doc)
    for seg, spans in by_seg.items():
        path, text = targets[seg]
        node = new
        for k in path[:-1]:
            node = node[k]
        node[path[-1]] = redact_text(text, spans)
    return JsonOut(orjson.dumps(new), decision, len(targets))
