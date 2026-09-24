"""Apply Decision.transformations to the request and VERIFY the bytes before dispatch.

Exactly one redactor. Verification is terminal: (1) re-scan the transformed
segments (via the injected verifier: canonicalize + the same matcher) for every
redacted detector -> zero residual matches; (2) no original redacted value
appears in the serialized body that would leave the gateway.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import orjson

from rvproto.domain.decision import Transformation
from rvproto.domain.ports import PayloadVerifier
from rvproto.domain.request import ChatRequest
from rvproto.domain.text import redact_text


@dataclass(frozen=True, slots=True)
class Transformed:
    body: bytes
    verified: bool
    detail: str


def _set(doc: Any, path: tuple[str | int, ...], value: str) -> None:
    node = doc
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value


def apply_and_verify(
    chat: ChatRequest,
    transformations: Sequence[Transformation],
    verifier: PayloadVerifier,
) -> Transformed:
    by_seg: dict[int, list[tuple[int, int, str]]] = {}
    for t in transformations:
        by_seg.setdefault(t.span.segment, []).append((t.span.start, t.span.end, t.replacement))
    doc = copy.deepcopy(chat.doc)
    new_texts: list[str] = []
    raw_values: list[str] = []
    for seg, spans in by_seg.items():
        segment = chat.segments[seg]
        raw_values.extend(segment.text[a:b] for a, b, _ in spans)
        new = redact_text(segment.text, spans)
        new_texts.append(new)
        _set(doc, segment.path, new)
    body = orjson.dumps(doc)
    detectors = frozenset(t.detector for t in transformations)
    residual = verifier.residual_hits(new_texts, detectors)
    leaked = sum(1 for v in raw_values if v and v.encode() in body)
    ok = residual == 0 and leaked == 0 and bool(transformations)
    return Transformed(body, ok, f"spans={len(transformations)} residual={residual} leaked={leaked}")
