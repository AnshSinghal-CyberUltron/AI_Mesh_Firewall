"""Streamed tool-call channels and the :class:`ToolCallAssembler` (GW34, design "Response side").

PURE wire logic (no HTTP object, no I/O except the injected ``decoded_clean`` scan callable).

**Channel grammar.** The decoder mints ``output_tool_id#n``, ``output_tool_name#n`` and
``output_tool_arguments#n`` (``n`` = the call index); egress treats the keys as opaque, so every
call gets its own scan stream (per-call isolation). :func:`base_channel` maps a key back to the
domain channel. The legacy bare ``output_tool_arguments`` is index 0.

**The assembler** receives each RELEASED downstream frame (after egress redaction) and re-frames it
for the encoder:

* content / reasoning / refusal pass straight through (except JSON-mode content under an enforcing
  rule, which is withheld to :meth:`finish` and must ``json.loads`` after redaction, D9);
* tool calls are validated (id shape, declared name, ``tools_declared``); without an enforcing
  output rule each call is emitted from its first validated fragment on; WITH one, every call is
  withheld and released only by :meth:`finish`, once, in index order (D6a);
* the OUTPUT call indexes are dense and ascending in the order calls are first emitted (the stock
  OpenAI SDK's ``accumulate_delta`` merges tool-call fragments by list POSITION, so an upstream that
  interleaves calls or introduces index 2 before 0 would corrupt a call; the assembler re-numbers);
* every withheld byte is charged to one running total bounded by ``max_withheld_bytes`` and the
  injected ``charge`` / ``refund`` (the request's own coalescer), so the per-stream ceiling is not
  doubled (review R5-2).

Failures are :class:`Terminate` values (never raised): ``output_blocked`` (policy: undeclared name,
bad id, invalid JSON after redaction, residual finding), ``stream_malformed_upstream`` (structure:
a gap, an index or size bound), ``scan_failure`` (the injected scan raised). Fail closed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from gateway_v2.domain import posture
from gateway_v2.edge.wire.sse_chunks import ChunkEvent, ToolFragment
from gateway_v2.egress.stream import DownstreamFrame
from gateway_v2.runtime.resources_tools import ToolBounds

__all__ = (
    "FINISH_CHANNEL",
    "OUTPUT_BLOCKED",
    "SCAN_FAILURE",
    "TOOL_ARGUMENTS",
    "TOOL_ID",
    "TOOL_NAME",
    "ToolCallAssembler",
    "Terminate",
    "base_channel",
    "check_calls",
    "decoded_text",
    "parse_tool_key",
    "tool_key",
)

TOOL_ID = "output_tool_id"
TOOL_NAME = "output_tool_name"
TOOL_ARGUMENTS = "output_tool_arguments"
FINISH_CHANNEL = "finish_reason"
#: Terminal codes (the shipped spellings of ``edge/error_frame_scan``; re-declared to stay pure).
OUTPUT_BLOCKED = "output_blocked"
SCAN_FAILURE = "scan_failure"
_MARK = "#"
_BASES = frozenset({TOOL_ID, TOOL_NAME, TOOL_ARGUMENTS})
_CONTENT = "content"
_OUTPUT_CONTENT = "output_content"
_REASONING = frozenset({"reasoning_content", "output_reasoning"})
_REFUSAL = frozenset({"refusal", "output_refusal"})
_ROLE = "role"

_ID_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{1,64}")


def tool_key(base: str, index: int) -> str:
    """The per-call channel key for ``base`` and call ``index``."""
    return f"{base}{_MARK}{index}"


def parse_tool_key(key: str) -> tuple[str, int] | None:
    """``(base, index)`` for a tool channel key, else ``None``. The bare legacy key is index 0."""
    if key in _BASES:
        return key, 0
    base, mark, tail = key.partition(_MARK)
    if mark and base in _BASES and tail.isascii() and tail.isdigit():
        return base, int(tail)
    return None


def base_channel(key: str) -> str:
    """The domain channel a (possibly ``#n``-suffixed) key belongs to."""
    return key.partition(_MARK)[0]


@dataclass(frozen=True, slots=True)
class Terminate:
    """Stop the stream with the posture ``code``; nothing of the offending call is released."""

    code: str


class _Call:
    """One call's accumulated parts (mutable, per request)."""

    __slots__ = ("arguments", "id", "name", "out_index", "started")

    def __init__(self) -> None:
        self.out_index = -1
        self.id = ""
        self.name = ""
        self.arguments = ""
        self.started = False


def check_calls(
    calls: Mapping[int, _Call],
    *,
    declared: frozenset[str],
    tools_declared: bool,
    finish_reason: str | None,
) -> Terminate | None:
    """Structural gaps first (``stream_malformed_upstream``), then policy (``output_blocked``)."""
    if finish_reason == "tool_calls" and not calls:
        return Terminate(posture.STREAM_MALFORMED_UPSTREAM)
    for call in calls.values():
        if not call.id or not call.name:
            return Terminate(posture.STREAM_MALFORMED_UPSTREAM)
    for call in calls.values():
        bad_name = not tools_declared or call.name not in declared
        if bad_name or not _ID_PATTERN.fullmatch(call.id):
            return Terminate(OUTPUT_BLOCKED)
    return None


def decoded_text(arguments: str) -> str | None:
    """The decoded string leaves and keys of ``arguments`` (``None`` if it is not JSON).

    Empty / whitespace-only arguments are ``{}``. Joined with newlines so ONE scan covers the
    decoded form (an escape-obfuscated value the raw-text scan missed).
    """
    try:
        value = json.loads(arguments if arguments.strip() else "{}")
    except (ValueError, RecursionError):
        return None
    leaves: list[str] = []
    stack: list[object] = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            leaves.append(node)
        elif isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, dict):
            for key, item in node.items():
                leaves.append(str(key))
                stack.append(item)
    return "\n".join(leaves)


class ToolCallAssembler:
    """Validates and re-frames released frames; per request, mutable (like ``_TerminalLatch``)."""

    __slots__ = (
        "_bounds",
        "_calls",
        "_charge",
        "_content",
        "_declared",
        "_decoded_clean",
        "_enforcing",
        "_finish",
        "_held",
        "_json_mode",
        "_refund",
        "_released_calls",
        "_tools_declared",
    )

    def __init__(
        self,
        *,
        declared: frozenset[str],
        tools_declared: bool,
        enforcing: bool,
        json_mode: bool,
        bounds: ToolBounds,
        decoded_clean: Callable[[str], Awaitable[bool]],
        charge: Callable[[int], bool],
        refund: Callable[[int], None],
    ) -> None:
        self._declared = declared
        self._tools_declared = tools_declared
        self._enforcing = enforcing
        self._json_mode = json_mode
        self._bounds = bounds
        self._decoded_clean = decoded_clean
        self._charge = charge
        self._refund = refund
        self._calls: dict[int, _Call] = {}
        self._content: list[str] = []
        self._finish: str | None = None
        self._held = 0
        self._released_calls = 0

    @property
    def finish_reason(self) -> str:
        """The final chunk's reason: upstream's, else ``tool_calls`` if a call was released."""
        if self._finish is not None:
            return self._finish
        return "tool_calls" if self._released_calls else "stop"

    @property
    def calls_released(self) -> int:
        return self._released_calls

    @property
    def calls_withheld(self) -> int:
        return 0 if self._released_calls else len(self._calls)

    def _hold(self, nbytes: int) -> bool:
        if self._held + nbytes > self._bounds.max_withheld_bytes or not self._charge(nbytes):
            return False
        self._held += nbytes
        return True

    def _release(self, nbytes: int) -> None:
        nbytes = min(nbytes, self._held)
        self._held -= nbytes
        if nbytes:
            self._refund(nbytes)

    def feed(self, frame: DownstreamFrame) -> tuple[ChunkEvent, ...] | Terminate:
        """Absorb one released frame; return the events to encode now, or ``Terminate``."""
        content = reasoning = refusal = ""
        extra: list[tuple[str, str]] = []
        touched: list[int] = []
        for channel, text in frame.text_deltas:
            if channel == FINISH_CHANNEL:
                self._finish = text
            elif channel == _ROLE:
                continue
            elif (key := parse_tool_key(channel)) is not None or _MARK in channel:
                if key is None:
                    return Terminate(posture.STREAM_MALFORMED_UPSTREAM)
                failure = self._absorb(key[0], key[1], text)
                if failure is not None:
                    return failure
                touched.append(key[1])
            elif channel in (_CONTENT, _OUTPUT_CONTENT):
                if self._json_mode and self._enforcing:
                    if not self._hold(len(text.encode())):
                        return Terminate(OUTPUT_BLOCKED)
                    self._content.append(text)
                else:
                    content += text
            elif channel in _REASONING:
                reasoning += text
            elif channel in _REFUSAL:
                refusal += text
            else:
                extra.append((channel, text))
        fragments: tuple[ToolFragment, ...] = ()
        if touched and not self._enforcing:
            emitted = self._emit_started(touched)
            if isinstance(emitted, Terminate):
                return emitted
            fragments = emitted
        if not (content or reasoning or refusal or extra or fragments):
            return ()
        return (
            ChunkEvent(
                content=content, reasoning=reasoning, refusal=refusal,
                tool_calls=fragments, extra=tuple(extra),
            ),
        )

    def _absorb(self, base: str, index: int, text: str) -> Terminate | None:
        if index >= self._bounds.max_tool_calls:
            return Terminate(posture.STREAM_MALFORMED_UPSTREAM)
        call = self._calls.setdefault(index, _Call())
        if base == TOOL_ARGUMENTS and not call.started:
            if len(call.arguments) + len(text) > self._bounds.arguments_bytes:
                return Terminate(posture.STREAM_MALFORMED_UPSTREAM)
        if not call.started or self._enforcing:
            if not self._hold(len(text.encode())):
                return Terminate(posture.STREAM_MALFORMED_UPSTREAM)
        if base == TOOL_ID:
            call.id += text
        elif base == TOOL_NAME:
            call.name += text
        else:
            call.arguments += text
        return None

    def _emit_started(self, touched: list[int]) -> tuple[ToolFragment, ...] | Terminate:
        """Non-enforcing: first fragment once id and name validate, then argument deltas."""
        fragments: list[ToolFragment] = []
        for index in dict.fromkeys(touched):
            call = self._calls[index]
            if not call.started:
                if not (call.id and call.name):
                    continue
                failure = check_calls(
                    {index: call}, declared=self._declared,
                    tools_declared=self._tools_declared, finish_reason=None,
                )
                if failure is not None:
                    return failure
                call.started = True
                call.out_index = self._released_calls
                self._released_calls += 1
                self._release(len((call.id + call.name + call.arguments).encode()))
                fragments.append(
                    ToolFragment(
                        index=call.out_index, id=call.id, name=call.name,
                        arguments=call.arguments,
                    )
                )
                call.arguments = ""
            elif call.arguments:
                fragments.append(ToolFragment(index=call.out_index, arguments=call.arguments))
                call.arguments = ""
        return tuple(fragments)

    async def finish(self) -> tuple[ChunkEvent, ...] | Terminate:
        """The ONE release point under an enforcing rule; the gap check in both modes (D6a)."""
        events: list[ChunkEvent] = []
        gaps = check_calls(
            self._calls, declared=self._declared, tools_declared=self._tools_declared,
            finish_reason=self._finish,
        )
        if gaps is not None:
            return gaps
        if self._json_mode and self._enforcing:
            text = "".join(self._content)
            try:
                json.loads(text)
            except (ValueError, RecursionError):
                return Terminate(OUTPUT_BLOCKED)
            events.append(ChunkEvent(content=text))
            self._release(len(text.encode()))
            self._content = []
        if not self._enforcing:
            return tuple(events)
        for position, index in enumerate(sorted(self._calls)):
            call = self._calls[index]
            decoded = decoded_text(call.arguments)
            if decoded is None:
                return Terminate(OUTPUT_BLOCKED)
            try:
                clean = await self._decoded_clean(decoded)
            except Exception:
                return Terminate(SCAN_FAILURE)
            if not clean:
                return Terminate(OUTPUT_BLOCKED)
            arguments = call.arguments if call.arguments.strip() else "{}"
            events.append(
                ChunkEvent(
                    tool_calls=(
                        ToolFragment(
                            index=position, id=call.id, name=call.name, arguments=arguments
                        ),
                    )
                )
            )
            self._released_calls += 1
            self._release(len((call.id + call.name + call.arguments).encode()))
            call.started = True
        return tuple(events)
