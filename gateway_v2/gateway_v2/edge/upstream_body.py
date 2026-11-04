"""Render the OpenAI request the provider receives from VERIFIED deltas (GW36 S1).

:func:`with_openai_body` runs after ``stage_upstream`` has applied and byte-verified the input
decision. It fills each :class:`~gateway_v2.edge.wire.openai_fold.TextSlot` of the request's
upstream template with verified delta ``i`` and adds the rendered request to the upstream body
under :data:`~gateway_v2.dispatch.openai_upstream.BODY_OPENAI_KEY`. The template holds no request
text, so every text byte the provider receives is a verified (masked where decided) delta.

A slot whose channel differs from its delta, or a delta count that differs from the template's
pair count, returns ``None`` and the caller refuses with zero provider calls; a joined pair that
cannot be split back safely returns :class:`FillRefused` (``input_unmaskable``).

Output budget, v1 parity (``gateway/ai_mesh_gateway/main.py`` "Max response tokens"): a
``max_tokens`` the client set is forwarded clamped; otherwise a client ``max_completion_tokens`` is
forwarded clamped to the same ceiling (and ``max_tokens`` stays absent: reasoning models reject
both); otherwise ``max_tokens`` is the ceiling.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from gateway_v2.dispatch.openai_upstream import BODY_ENDPOINT_KEY, BODY_OPENAI_KEY, CHAT_ENDPOINT
from gateway_v2.dispatch.transform import BODY_DELTAS_KEY
from gateway_v2.edge.input_enforcement import REFUSE_INPUT_UNMASKABLE
from gateway_v2.edge.upstream_tools import Unfillable, render_message_extras, render_tool_fields
from gateway_v2.edge.wire.openai import CanonicalRequest
from gateway_v2.edge.wire.openai_template import (
    JOIN_SEPARATOR,
    JOINED,
    TextSlot,
    UpstreamTemplate,
)
from gateway_v2.runtime.resources import ResourceContract

__all__ = ("FillRefused", "with_openai_body")

#: Text parts of one message are sent as ONE string joined with no separator: the scanned bytes.
_PART_JOIN = ""
#: The joined channel whose parts are JSON (the canonical assistant tool-call arguments).
_ARGUMENTS_CHANNEL = "tool_arguments"


@dataclass(frozen=True, slots=True)
class FillRefused:
    """The template cannot be filled safely: refuse with ``code`` and make zero provider calls."""

    code: str


def with_openai_body(
    upstream_body: Mapping[str, object],
    req: CanonicalRequest,
    contract: ResourceContract,
) -> Mapping[str, object] | FillRefused | None:
    """``upstream_body`` plus the rendered OpenAI request, or ``None`` on a slot mismatch.

    A request with no template (built by hand rather than parsed) is returned unchanged: the
    OpenAI provider then refuses it (``upstream_request_unbuildable``) without posting. The delta
    count must equal the template's pair count; only FORWARDED pairs are filled (scan-only pairs
    -- names, ids, structural runs, raw arguments -- never reach the body). A joined pair is split
    back on the join separator after the decision is applied; a part count that changed, an
    arguments part that is no longer JSON, or an object-key collision after redaction is
    :class:`FillRefused` (``input_unmaskable``, fail closed).
    """
    template = req.upstream_template
    if template is None:
        return upstream_body
    texts = _verified_texts(upstream_body.get(BODY_DELTAS_KEY))
    if texts is None or len(texts) != template.pair_count:
        return None
    joined = dict(template.joined)

    def fill(slot: TextSlot) -> str:
        if slot.index == JOINED:
            position = joined.get(slot.channel)
            if position is None:
                raise Unfillable
            channel, text = texts[position]
            parts = text.split(JOIN_SEPARATOR)
            if channel != slot.channel or len(parts) != template.pair_parts[position]:
                raise Unfillable(REFUSE_INPUT_UNMASKABLE)
            part = parts[slot.part]
            if slot.channel == _ARGUMENTS_CHANNEL and not _is_json(part):
                raise Unfillable(REFUSE_INPUT_UNMASKABLE)
            return part
        channel, text = texts[slot.index]
        if channel != slot.channel:
            raise Unfillable
        return text

    try:
        body = _render(template, req, contract, fill)
    except Unfillable as refused:
        return None if refused.code is None else FillRefused(refused.code)
    return MappingProxyType(
        {
            **upstream_body,
            BODY_OPENAI_KEY: MappingProxyType(body),
            BODY_ENDPOINT_KEY: CHAT_ENDPOINT,
        }
    )


def _is_json(text: str) -> bool:
    try:
        json.loads(text)
    except (ValueError, RecursionError):
        return False
    return True


def _render(
    template: UpstreamTemplate,
    req: CanonicalRequest,
    contract: ResourceContract,
    fill: Callable[[TextSlot], str],
) -> dict[str, object]:
    messages: list[Mapping[str, object]] = []
    for message in template.messages:
        content: str | None
        if message.content is None:
            content = None
        elif isinstance(message.content, TextSlot):
            content = fill(message.content)
        else:
            content = _PART_JOIN.join(fill(slot) for slot in message.content)
        entry: dict[str, object] = {"role": message.role, "content": content}
        entry.update(render_message_extras(message, fill))
        messages.append(MappingProxyType(entry))
    body: dict[str, object] = {"model": req.model, "messages": tuple(messages), **req.forwarded}
    body.update(render_tool_fields(template.tools, fill, collision_code=REFUSE_INPUT_UNMASKABLE))
    if template.stop is not None:
        stop_slots = (template.stop,) if isinstance(template.stop, TextSlot) else template.stop
        stops = [fill(slot) for slot in stop_slots]
        body["stop"] = stops[0] if isinstance(template.stop, TextSlot) else tuple(stops)
    _apply_budget(body, req, contract)
    body["stream"] = True
    return body


def _verified_texts(raw: object) -> Sequence[tuple[str, str]] | None:
    if not isinstance(raw, (tuple, list)):
        return None
    pairs: list[tuple[str, str]] = []
    for item in raw:
        if not (isinstance(item, (tuple, list)) and len(item) == 2):
            return None
        channel, text = item
        if not (isinstance(channel, str) and isinstance(text, str)):
            return None
        pairs.append((channel, text))
    return pairs


def _apply_budget(
    body: dict[str, object], req: CanonicalRequest, contract: ResourceContract
) -> None:
    ceiling = min(contract.default_response_token_ceiling(), contract.max_output_tokens())
    if req.max_tokens > 0:
        body["max_tokens"] = req.max_tokens
    elif req.max_completion_tokens is not None:
        body["max_completion_tokens"] = min(req.max_completion_tokens, ceiling)
    else:
        body["max_tokens"] = ceiling
