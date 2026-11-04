"""SDK chunk value types, split out of `edge/wire/sse.py` (size gate; a pure move).

`edge/wire/sse.py` re-exports every public name here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

__all__ = (
    "CHUNK_OBJECT",
    "DEFAULT_CREATED",
    "ChunkEvent",
    "SSEChoice",
    "SSEChunk",
    "StreamIdentity",
    "ToolFragment",
)

#: The ``object`` discriminator every streamed content frame carries. The stock
#: OpenAI SDK parses a streamed frame as a ``ChatCompletionChunk`` only when this
#: field reads exactly ``chat.completion.chunk`` (matches the recorded
#: conformance frame in ``tests/openai_conformance/test_openai_sdk_compat.py``).
CHUNK_OBJECT = "chat.completion.chunk"

#: The default ``created`` epoch when the caller supplies none.
DEFAULT_CREATED = 0


@dataclass(frozen=True, slots=True)
class SSEChoice:
    """One SDK ``choices[]`` entry: an index, a ``delta`` object, a finish reason.

    ``delta`` is a read-only ``Mapping`` (e.g. ``{"content": "Hello"}`` or
    ``{"role": "assistant", "content": "Hello"}``) carried straight into the
    serialised JSON. ``finish_reason`` is ``None`` on every content chunk and set
    (``"stop"``) only on the terminal content chunk, matching the SDK schema.
    """

    index: int
    delta: Mapping[str, object]
    finish_reason: str | None = None

    def as_json_obj(self) -> dict[str, object]:
        """The plain JSON object for this choice (SDK ``choices[]`` entry)."""
        return {
            "index": self.index,
            "delta": dict(self.delta),
            "finish_reason": self.finish_reason,
        }


@dataclass(frozen=True, slots=True)
class SSEChunk:
    """The SDK-shaped content frame body serialised to ``data: {json}\\n\\n``.

    Carries the OpenAI ``chat.completion.chunk`` fields the stock SDK requires to
    parse a streamed frame: ``id`` / ``object`` / ``created`` / ``model`` and the
    ``choices`` list. Frozen + slotted; ``choices`` is a tuple so the value type
    is immutable.
    """

    id: str
    model: str
    choices: tuple[SSEChoice, ...]
    created: int = DEFAULT_CREATED

    def as_json_obj(self) -> dict[str, object]:
        """The plain JSON object the encoder serialises for a content frame."""
        return {
            "id": self.id,
            "object": CHUNK_OBJECT,
            "created": self.created,
            "model": self.model,
            "choices": [choice.as_json_obj() for choice in self.choices],
        }


@dataclass(frozen=True, slots=True)
class ToolFragment:
    """One streamed ``delta.tool_calls[]`` entry (``None`` fields are omitted on the wire)."""

    index: int
    id: str | None = None
    name: str | None = None
    arguments: str | None = None


@dataclass(frozen=True, slots=True)
class ChunkEvent:
    """One merged downstream chunk: every delta of one frame, as typed fields.

    ``extra`` carries any other string-valued channel under its own name (the legacy
    channel-to-delta-key behaviour). ``finish_reason`` is set only on a final chunk.
    """

    content: str = ""
    reasoning: str = ""
    refusal: str = ""
    tool_calls: tuple[ToolFragment, ...] = ()
    extra: tuple[tuple[str, str], ...] = ()
    finish_reason: str | None = None


@dataclass(frozen=True, slots=True)
class StreamIdentity:
    """The stable ``id`` / ``model`` / ``created`` every chunk of one stream carries."""

    stream_id: str
    model: str
    created: int
