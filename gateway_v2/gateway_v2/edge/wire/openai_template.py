"""Template value types and the fold builder shared by the OpenAI fold and the tools fold (GW34).

Split out of ``openai_fold.py`` so ``openai_tools.py`` (validation + schema classification) and
``openai_fold.py`` (the one fold loop) can both use them without an import cycle. PURE.

**Plain slots vs joined runs.** A plain :class:`TextSlot` is one fold pair (as in GW36 S1). The
tool/function channels use JOINED pairs instead: every string of one channel in the whole request
is joined with :data:`JOIN_SEPARATOR` into ONE fold pair, so a request with many tools costs a
handful of detector invocations (design "ToolBounds values": the detector's fixed per-call cost,
not the byte count, bounds the scan budget). A forwarded joined pair is split back on the
separator after the input decision is applied; the template refers to a part by
``TextSlot(index=JOINED, channel, part)``. A scan-only joined pair (names, ids, structural
strings, raw arguments) has no slots at all.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = (
    "JOIN_SEPARATOR",
    "JOINED",
    "JOINED_CHANNEL_ORDER",
    "SCAN_ONLY_CHANNELS",
    "Builder",
    "FoldResult",
    "SchemaObject",
    "TemplateChoice",
    "TemplateMessage",
    "TemplateResponseFormat",
    "TemplateTool",
    "TemplateToolCall",
    "TextSlot",
    "ToolsTemplate",
    "UpstreamTemplate",
)

#: ASCII record separator: joins the strings of one joined channel. A request string that
#: contains it is refused (``openai_tools``), so a split can never be ambiguous.
JOIN_SEPARATOR = "\x1e"
#: ``TextSlot.index`` of a slot that is a PART of a joined pair (resolved via the template).
JOINED = -1

#: The order joined pairs are appended after the plain pairs (a fixed order independent of content).
JOINED_CHANNEL_ORDER = (
    "tool_name",
    "tool_call_id",
    "tool_schema_struct",
    "tool_arguments_raw",
    "tool_description",
    "tool_schema_text",
    "response_format_schema",
    "tool_arguments",
)
#: Joined channels whose pair is never forwarded (decided alone: BLOCK-only or raw).
SCAN_ONLY_CHANNELS = frozenset(
    {"tool_name", "tool_call_id", "tool_schema_struct", "tool_arguments_raw"}
)


@dataclass(frozen=True, slots=True)
class FoldResult:
    """Folded ``(channel_name, text)`` pairs in document order + parallel per-pair facts.

    ``message_index`` is the ``messages[]`` index each pair came from (a ``prompt`` / ``stop`` /
    joined pair gets an index past the last message, so no join run spans them). ``forwarded`` is
    False for a pair that is scanned but never sent upstream (names, ids, raw arguments,
    structural runs). All tuples have the same length.
    """

    pairs: tuple[tuple[str, str], ...]
    message_index: tuple[int, ...]
    forwarded: tuple[bool, ...] = ()


@dataclass(frozen=True, slots=True)
class TextSlot:
    """Placeholder for fold pair ``index`` (whose channel is ``channel``) in the template.

    ``index == JOINED`` marks part ``part`` of the joined pair of ``channel``. Not a JSON value,
    so nothing parsed from a request body can be mistaken for one.
    """

    index: int
    channel: str
    part: int = 0


@dataclass(frozen=True, slots=True)
class SchemaObject:
    """An ordered JSON object in the template; a key is a literal or (free-text) a TextSlot."""

    items: tuple[tuple[object, object], ...]


#: A message ``content`` in the template: one slot (string content), a tuple of slots (text parts,
#: rendered joined with no separator -- the scanned byte sequence), or ``None`` (absent / null).
ContentTemplate = TextSlot | tuple[TextSlot, ...] | None


@dataclass(frozen=True, slots=True)
class TemplateToolCall:
    """One assistant history tool call: ``id`` / ``name`` literals + the arguments slot."""

    id: str
    name: str
    arguments: TextSlot


@dataclass(frozen=True, slots=True)
class TemplateMessage:
    """One forwarded message: its role, content template and (GW34) tool-call facts."""

    role: str
    content: ContentTemplate
    tool_calls: tuple[TemplateToolCall, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class TemplateTool:
    """One declared function: literal name, optional description slot, the schema node."""

    name: str
    description: TextSlot | None
    parameters: object
    strict: bool | None


@dataclass(frozen=True, slots=True)
class TemplateChoice:
    """``tool_choice`` as a literal (``none``/``auto``/``required``) or a named function."""

    literal: str | None
    name: str | None


@dataclass(frozen=True, slots=True)
class TemplateResponseFormat:
    """``response_format`` (``text`` / ``json_object`` / ``json_schema``) from validated parts."""

    type: str
    name: str | None = None
    description: TextSlot | None = None
    schema: object = None
    strict: bool | None = None


@dataclass(frozen=True, slots=True)
class ToolsTemplate:
    """The forwardable tool fields: tools, tool_choice, parallel_tool_calls, response_format."""

    tools: tuple[TemplateTool, ...] = ()
    tool_choice: TemplateChoice | None = None
    parallel_tool_calls: bool | None = None
    response_format: TemplateResponseFormat | None = None


@dataclass(frozen=True, slots=True)
class UpstreamTemplate:
    """The forwardable request structure with every scanned text occurrence as a :class:`TextSlot`.

    ``slot_count`` equals the number of fold pairs, so a renderer can check it was handed exactly
    the deltas this template was built against. ``forwarded`` is the per-pair forwarded flag;
    ``pair_parts`` is the part count of each joined pair (0 for a plain pair); ``joined`` maps a
    joined channel to its pair index.
    """

    messages: tuple[TemplateMessage, ...]
    stop: TextSlot | tuple[TextSlot, ...] | None
    slot_count: int
    tools: ToolsTemplate = ToolsTemplate()
    forwarded: tuple[bool, ...] = ()
    pair_parts: tuple[int, ...] = ()
    joined: tuple[tuple[str, int], ...] = ()

    @property
    def pair_count(self) -> int:
        return self.slot_count


class Builder:
    """Accumulates fold pairs and hands out the matching slots (one loop, one order)."""

    __slots__ = ("_joined", "_scan_only_plain", "index", "pairs")

    def __init__(self) -> None:
        self.pairs: list[tuple[str, str]] = []
        self.index: list[int] = []
        self._joined: dict[str, list[str]] = {}
        self._scan_only_plain: list[int] = []

    def slot(
        self, channel: str, text: str, message_index: int, *, forwarded: bool = True
    ) -> TextSlot:
        slot = TextSlot(index=len(self.pairs), channel=channel)
        self.pairs.append((channel, text))
        self.index.append(message_index)
        if not forwarded:
            self._scan_only_plain.append(slot.index)
        return slot

    def scan_only(self, channel: str, text: str) -> None:
        """Add ``text`` to the joined scan-only pair of ``channel`` (no slot)."""
        self._joined.setdefault(channel, []).append(text)

    def joined_slot(self, channel: str, text: str) -> TextSlot:
        """Add ``text`` to the joined forwarded pair of ``channel``; return its part slot."""
        parts = self._joined.setdefault(channel, [])
        parts.append(text)
        return TextSlot(index=JOINED, channel=channel, part=len(parts) - 1)

    def joined_slot_count(self) -> int:
        return len(self._joined)

    def finish(
        self, next_message_index: int
    ) -> tuple[FoldResult, tuple[bool, ...], tuple[int, ...], tuple[tuple[str, int], ...]]:
        """Append the joined pairs and return the fold + (forwarded, pair_parts, joined)."""
        forwarded = [True] * len(self.pairs)
        for position in self._scan_only_plain:
            forwarded[position] = False
        parts = [0] * len(self.pairs)
        joined: list[tuple[str, int]] = []
        for channel in JOINED_CHANNEL_ORDER:
            texts = self._joined.get(channel)
            if not texts:
                continue
            joined.append((channel, len(self.pairs)))
            self.pairs.append((channel, JOIN_SEPARATOR.join(texts)))
            self.index.append(next_message_index)
            forwarded.append(channel not in SCAN_ONLY_CHANNELS)
            parts.append(len(texts))
        fold = FoldResult(
            pairs=tuple(self.pairs), message_index=tuple(self.index), forwarded=tuple(forwarded)
        )
        return fold, tuple(forwarded), tuple(parts), tuple(joined)
