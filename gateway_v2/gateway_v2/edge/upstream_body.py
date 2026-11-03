"""Render the OpenAI request the provider receives from VERIFIED deltas (GW36 S1).

:func:`with_openai_body` runs after ``stage_upstream`` has applied and byte-verified the input
decision. It fills each :class:`~gateway_v2.edge.wire.openai_fold.TextSlot` of the request's
upstream template with verified delta ``i`` and adds the rendered request to the upstream body
under :data:`~gateway_v2.dispatch.openai_upstream.BODY_OPENAI_KEY`. The template holds no request
text, so every text byte the provider receives is a verified (masked where decided) delta.

A slot whose channel differs from its delta, or a delta count that differs from the template's
slot count, returns ``None`` and the caller refuses with zero provider calls.

Output budget, v1 parity (``gateway/ai_mesh_gateway/main.py`` "Max response tokens"): a
``max_tokens`` the client set is forwarded clamped; otherwise a client ``max_completion_tokens`` is
forwarded clamped to the same ceiling (and ``max_tokens`` stays absent: reasoning models reject
both); otherwise ``max_tokens`` is the ceiling.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType

from gateway_v2.dispatch.openai_upstream import BODY_ENDPOINT_KEY, BODY_OPENAI_KEY, CHAT_ENDPOINT
from gateway_v2.dispatch.transform import BODY_DELTAS_KEY
from gateway_v2.edge.wire.openai import CanonicalRequest
from gateway_v2.edge.wire.openai_fold import TextSlot
from gateway_v2.runtime.resources import ResourceContract

__all__ = ("with_openai_body",)

#: Text parts of one message are sent as ONE string joined with no separator: the scanned bytes.
_PART_JOIN = ""


def with_openai_body(
    upstream_body: Mapping[str, object],
    req: CanonicalRequest,
    contract: ResourceContract,
) -> Mapping[str, object] | None:
    """``upstream_body`` plus the rendered OpenAI request, or ``None`` on a slot mismatch.

    A request with no template (built by hand rather than parsed) is returned unchanged: the
    OpenAI provider then refuses it (``upstream_request_unbuildable``) without posting.
    """
    template = req.upstream_template
    if template is None:
        return upstream_body
    texts = _verified_texts(upstream_body.get(BODY_DELTAS_KEY))
    if texts is None or len(texts) != template.slot_count:
        return None

    def fill(slot: TextSlot) -> str | None:
        channel, text = texts[slot.index]
        return text if channel == slot.channel else None

    messages: list[Mapping[str, object]] = []
    for message in template.messages:
        content: str | None
        if message.content is None:
            content = None
        elif isinstance(message.content, TextSlot):
            content = fill(message.content)
            if content is None:
                return None
        else:
            parts = [fill(slot) for slot in message.content]
            if any(part is None for part in parts):
                return None
            content = _PART_JOIN.join(part for part in parts if part is not None)
        messages.append(MappingProxyType({"role": message.role, "content": content}))

    body: dict[str, object] = {"model": req.model, "messages": tuple(messages), **req.forwarded}
    if template.stop is not None:
        stop_slots = (template.stop,) if isinstance(template.stop, TextSlot) else template.stop
        stops = [fill(slot) for slot in stop_slots]
        if any(stop is None for stop in stops):
            return None
        body["stop"] = stops[0] if isinstance(template.stop, TextSlot) else tuple(stops)
    _apply_budget(body, req, contract)
    body["stream"] = True
    return MappingProxyType(
        {
            **upstream_body,
            BODY_OPENAI_KEY: MappingProxyType(body),
            BODY_ENDPOINT_KEY: CHAT_ENDPOINT,
        }
    )


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
