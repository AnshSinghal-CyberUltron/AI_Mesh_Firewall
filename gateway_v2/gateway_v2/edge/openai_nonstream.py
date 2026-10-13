"""Non-streaming collect + output-enforce helpers (GW15, task 4.1).

Split out of ``edge/openai_chat.py`` so the chat-surface module stays under the
800-line module gate: this sibling owns the OUTPUT-side mechanics of the
non-streaming path — draining the injected ``ProviderClient`` to the final event,
scanning + resolving + enforcing every Output_Channel, and reducing the result to
either the enforced ``(channel, text)`` deltas the renderer places into ONE OpenAI
JSON body or a fail-closed value-code. ``ChatSurface.collect_and_render`` (in
``edge/openai_chat.py``) is the thin public entry that calls :func:`collect_and_enforce`
and then renders / withholds through the single ``edge/errors`` envelope.

``edge`` is the top of the import-linter layer order, so this module may import
``egress`` (the pure ``apply_decision`` + ``OutputBlocked``), ``dispatch`` (the
``UpstreamRequest`` value + the injected ``ProviderClient`` surface), and its
sibling ``edge`` modules. ``egress`` never names ``detect`` — the findings producer
(``ChatRoute.error_scanner``) and the resolver (``ChatRoute.resolver``) arrive
injected on the :class:`~gateway_v2.edge.routes.ChatRoute` bundle, in ``edge``,
above ``detect`` (R7.5 / R14.1).

**Fail closed everywhere.** A BLOCK disposition in any output channel withholds the
whole body; an uncomputable scan / decision (an ``apply_decision`` ``ValueError`` on
a partially-covered redaction span, or any scanner / resolver / provider raise) also
withholds — the raw collected bytes are NEVER emitted when the decision could not be
computed or blocks, and no branch increments ``fail_open_total`` (R8.3 / R8.4).

**Import cycle.** ``ChatRoute`` lives in ``edge/routes.py`` which imports nothing
from here at runtime; it is referenced only in annotations under
:data:`typing.TYPE_CHECKING` (``from __future__ import annotations`` stringises
every annotation), so no runtime circular import is formed.

**No capacity literal, no module-level mutable.** The output-content channel name
is a channel spelling, not a capacity-position literal; there is no module-level
list / dict / set.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from gateway_v2.dispatch.provider import UpstreamRequest
from gateway_v2.egress.output_guard import OutputBlocked, apply_decision

if TYPE_CHECKING:
    from gateway_v2.edge.routes import ChatRoute

__all__ = (
    "EnforcedOutput",
    "collect_and_enforce",
)

#: The output-content channel a mid-stream provider ``error_frame`` folds into, so a
#: provider error body is scanned as output content, never forwarded unscanned. The
#: spelling the ``OpenAIResponseRenderer`` maps to ``message.content``. A channel name,
#: not a capacity literal.
_OUTPUT_CONTENT_CHANNEL = "output_content"


@dataclass(frozen=True, slots=True)
class EnforcedOutput:
    """The result of collecting + enforcing the non-streaming upstream (task 4.1).

    Mirrors ``LifecycleOutcome``'s proceed/refuse discipline on the OUTPUT side:
    ``refuse_code is None`` with the enforced ``(channel, text)`` ``deltas`` for the
    renderer, OR a declared value-code (``REFUSE_OUTPUT_BLOCKED`` on a BLOCK,
    ``REFUSE_INPUT_SCAN_FAILURE`` on an uncomputable scan/decision) with empty
    ``deltas`` — the raw bytes are never emitted (fail closed, R8.3 / R8.4), and no
    branch increments ``fail_open_total``. Frozen + slotted.
    """

    deltas: tuple[tuple[str, str], ...]
    refuse_code: str | None


async def collect_and_enforce(
    deps: ChatRoute,
    destination: str,
    body: Mapping[str, object],
    *,
    block_code: str,
    scan_failure_code: str,
) -> EnforcedOutput:
    """Drain the provider, enforce every Output_Channel; fail closed (task 4.1).

    Returns the enforced ``(channel, text)`` deltas on success, or a fail-closed
    ``refuse_code`` — the raw collected bytes are never emitted when the decision
    could not be computed or blocks. A BLOCK ⇒ ``block_code`` (R8.3); any other raise
    (an ``apply_decision`` ``ValueError`` on a partially-covered span, a scanner /
    resolver / provider raise) ⇒ ``scan_failure_code`` (R8.4). The two codes are
    passed in by the caller so this helper carries no value-code spelling of its own
    (one spelling source, in ``edge/openai_chat.py``). No branch increments
    ``fail_open_total``.
    """
    try:
        collected = await _collect_output_channels(deps, destination, body)
        deltas = _enforce_channels(deps, collected)
    except OutputBlocked:
        return EnforcedOutput(deltas=(), refuse_code=block_code)
    except Exception:  # noqa: BLE001 — fail closed: any uncomputable scan/decision withholds.
        return EnforcedOutput(deltas=(), refuse_code=scan_failure_code)
    return EnforcedOutput(deltas=deltas, refuse_code=None)


async def _collect_output_channels(
    deps: ChatRoute,
    destination: str,
    body: Mapping[str, object],
) -> tuple[tuple[str, str], ...]:
    """Drain the provider to its final event, concatenating ``text_deltas`` per channel.

    Reads the async event stream to exhaustion, concatenating each channel's fragments
    in arrival order into one ``(channel, text)`` pair (first-seen channel order
    preserved so the render is deterministic). A mid-stream ``error_frame`` is folded
    into the output-content channel so a provider error body is scanned as content,
    never forwarded unscanned.
    """
    order: list[str] = []
    texts: dict[str, list[str]] = {}

    def _append(channel: str, text: str) -> None:
        if channel not in texts:
            texts[channel] = []
            order.append(channel)
        texts[channel].append(text)

    async for event in deps.provider.open(
        UpstreamRequest(destination=destination, body=body)
    ):
        for channel, delta in event.text_deltas:
            _append(channel, delta)
        if event.error_frame is not None:
            _append(_OUTPUT_CONTENT_CHANNEL, event.error_frame)
    return tuple((channel, "".join(texts[channel])) for channel in order)


def _enforce_channels(
    deps: ChatRoute,
    collected: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    """Scan + resolve + apply the output decision per channel (R8.1/R8.2).

    For each collected ``(channel, text)`` pair: run ``deps.error_scanner`` (the
    injected whole-text findings producer) over that channel's text, resolve via
    ``deps.resolver.decide`` (the C22 ``most_restrictive`` decision), and enforce with
    ``apply_decision(text, decision, base_offset=0)``. Scanning per channel keeps a
    finding's spans local to the channel the scanner saw, so a REDACT masks exactly
    that output field's bytes and the enforced text stays in its own channel for the
    renderer (channel fidelity, R8.2). A BLOCK raises ``OutputBlocked``; an
    uncomputable scan / decision or a partially-covered span propagates — both handled
    fail-closed by :func:`collect_and_enforce`.
    """
    enforced: list[tuple[str, str]] = []
    for channel, text in collected:
        decision = deps.resolver.decide(list(deps.error_scanner(text)))
        enforced.append((channel, apply_decision(text, decision, base_offset=0)))
    return tuple(enforced)
