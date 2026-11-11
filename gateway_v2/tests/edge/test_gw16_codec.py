# Feature: chat-surface-gw16-misc, Property 2: Misc parse/render round-trip preserves input items and model  # noqa: E501
# Validates: Requirements 2.1, 2.2
"""Property 2 — misc parse/render round-trip preserves input items and model.

House idiom: a seeded ``random.Random``, no ``hypothesis``, >=10,000 iterations.

*For any* well-formed ``POST /v1/embeddings`` or ``POST /v1/moderations`` body, parsing
it through :class:`MiscRequestParser` and then rendering the corresponding response
through :class:`MiscResponseRenderer` preserves every ``input`` item byte-for-byte in
request order and preserves ``model``, and yields the declared OpenAI structural
envelope that re-parses stably.

Two surfaces are exercised each iteration.

**Embeddings (R2.1).** A well-formed body is parsed into a
:class:`MiscEmbeddingsRequest`; the parsed ``input_items`` are compared byte-for-byte
and in order against the items the generator wrote. A provider vector is scripted for
every item and the response is rendered with :meth:`MiscResponseRenderer.embeddings_body`.
The rendered body re-parses to the declared envelope: ``object == "list"``, exactly one
``data`` entry per input item, each entry ``object == "embedding"`` with a
*monotonically increasing* ``index`` equal to its input position, the echoed ``model``,
and the recovered vectors byte-identical to the scripted ones (the index-order round
trip — R6.2/R6.3 structural shape).

**Moderations (R2.2).** A well-formed body (``input`` present, ``model`` optional) is
parsed into a :class:`MiscModerationsRequest`; the parsed ``input_items`` are compared
byte-for-byte and in order, and the optional ``model`` is preserved
(``None`` ↔ absent). A :class:`ModerationResult` is mapped from a scripted finding set
via :func:`moderation_result_from_findings` and rendered with
:meth:`MiscResponseRenderer.moderation_body`. The rendered body re-parses to the
declared envelope: an ``id`` prefixed ``modr-``, the echoed ``model``, and a single
``results`` row whose ``categories`` / ``category_scores`` are the mapped category map,
with ``flagged`` true exactly when at least one category is flagged.

The generators are smart: they emit only well-formed OpenAI-compatible bodies, draw
``input`` texts from a corpus that includes empty strings, ASCII, non-ASCII/unicode,
JSON-significant characters, and whitespace, cover both the single-string and the
list-of-strings ``input`` shapes, and (for moderations) cover both present and absent
``model`` plus EXECUTED and non-EXECUTED findings over every firewall ``Category``.
"""

from __future__ import annotations

import json
import random

from gateway_v2.domain.category import Category
from gateway_v2.domain.finding import Finding, FindingStatus, Span
from gateway_v2.edge.wire.openai_misc import (
    MODERATION_CATEGORY_MAP,
    MiscRequestParser,
    MiscResponseRenderer,
    ModerationResult,
    moderation_result_from_findings,
)

_SEED = 0x16_16_0002
_ITERATIONS = 10_000

_PARSER = MiscRequestParser()
_RENDERER = MiscResponseRenderer()

# A corpus that stresses byte-for-byte preservation: empties, ASCII, unicode (so the
# renderer's ``ensure_ascii`` escaping must round-trip), JSON-significant characters,
# control/whitespace, and emoji (astral code points -> surrogate escapes on the wire).
_TEXT_CORPUS: tuple[str, ...] = (
    "",
    "hello world",
    "SSN 123-45-6789",
    'quote " and backslash \\ and brace }{',
    "unicode: café — naïve © Ω λ",
    "日本語テキスト",
    "emoji 🙂🚀 end",
    "line1\nline2\ttab\r\n",
    "   leading and trailing   ",
    '{"nested":"json","as":"text"}',
    "a" * 64,
)

_MODELS: tuple[str, ...] = ("text-embedding-3-small", "text-embedding-3-large", "o3")

#: Every firewall ``Category`` — all currently ride as a moderation EXTRA keyed by the
#: ``Category`` spelling (see ``MODERATION_CATEGORY_MAP``).
_CATEGORIES: tuple[Category, ...] = tuple(Category)

_NON_EXECUTED: tuple[FindingStatus, ...] = (
    FindingStatus.SKIPPED,
    FindingStatus.UNAVAILABLE,
)


def _ctx(seed: int, i: int, **extra: object) -> str:
    body = " ".join(f"{k}={v!r}" for k, v in extra.items())
    return f"seed={seed:#x} iter={i} {body}"


def _text(rng: random.Random) -> str:
    return rng.choice(_TEXT_CORPUS)


def _gen_input_items(rng: random.Random) -> tuple[object, tuple[str, ...]]:
    """Emit a well-formed ``input`` JSON value + the ordered items it folds to.

    A single-string ``input`` folds to one item; a list ``input`` folds to one item
    per element, preserving request order.
    """
    if rng.random() < 0.5:
        text = _text(rng)
        return text, (text,)
    items = tuple(_text(rng) for _ in range(rng.randint(1, 5)))
    return list(items), items


def _gen_embeddings_body(rng: random.Random) -> tuple[bytes, str, tuple[str, ...]]:
    """A well-formed embeddings body + the model + ordered input items it carries."""
    model = rng.choice(_MODELS)
    input_value, items = _gen_input_items(rng)
    body = {"model": model, "input": input_value}
    return json.dumps(body).encode("utf-8"), model, items


def _gen_moderations_body(
    rng: random.Random,
) -> tuple[bytes, str | None, tuple[str, ...]]:
    """A well-formed moderations body + the optional model + ordered input items.

    ``model`` is OPTIONAL on ``/v1/moderations`` — the generator emits a present model
    or omits the key entirely (parsed as ``None``).
    """
    input_value, items = _gen_input_items(rng)
    body: dict[str, object] = {"input": input_value}
    model: str | None
    if rng.random() < 0.5:
        model = rng.choice(_MODELS)
        body["model"] = model
    else:
        model = None
    return json.dumps(body).encode("utf-8"), model, items


def _script_vectors(
    rng: random.Random, item_count: int
) -> tuple[tuple[int, tuple[float, ...]], ...]:
    """One ``(index, vector)`` pair per input item, index == input position."""
    return tuple(
        (
            index,
            tuple(rng.uniform(-1.0, 1.0) for _ in range(rng.randint(0, 4))),
        )
        for index in range(item_count)
    )


def _script_findings(rng: random.Random) -> tuple[Finding, ...]:
    """A random finding set over the firewall taxonomy (EXECUTED + non-EXECUTED)."""
    findings: list[Finding] = []
    for _ in range(rng.randint(0, 4)):
        category = rng.choice(_CATEGORIES)
        if rng.random() < 0.5:
            findings.append(
                Finding(
                    detector="d",
                    detector_version="1",
                    category=category,
                    status=FindingStatus.EXECUTED,
                    confidence=rng.uniform(0.0, 1.0),
                    spans=(Span(0, 1),),
                    evidence=None,
                )
            )
        else:
            findings.append(
                Finding(
                    detector="d",
                    detector_version="1",
                    category=category,
                    status=rng.choice(_NON_EXECUTED),
                    confidence=None,
                    spans=(),
                    evidence=None,
                )
            )
    return tuple(findings)


def test_property_2_embeddings_roundtrip_preserves_items_and_model() -> None:
    rng = random.Random(_SEED)
    for i in range(_ITERATIONS):
        raw, model, items = _gen_embeddings_body(rng)

        parsed = _PARSER.parse_embeddings(raw)

        # --- Parsed input items preserved byte-for-byte, in request order (R2.1). ----
        assert parsed.input_items == items, _ctx(
            _SEED, i, note="embeddings input items mismatch",
            got=parsed.input_items, want=items,
        )
        assert parsed.model == model, _ctx(
            _SEED, i, note="embeddings model mismatch", got=parsed.model, want=model
        )

        # --- Render + re-parse yields the declared embeddings envelope (R6.2/R6.3). --
        vectors = _script_vectors(rng, len(parsed.input_items))
        rendered = _RENDERER.embeddings_body(parsed, vectors)
        reparsed = json.loads(rendered)

        assert reparsed["object"] == "list", _ctx(
            _SEED, i, note="embeddings object not 'list'", got=reparsed.get("object")
        )
        assert reparsed["model"] == model, _ctx(
            _SEED, i, note="rendered model mismatch", got=reparsed.get("model")
        )

        data = reparsed["data"]
        # exactly one entry per input item.
        assert len(data) == len(parsed.input_items), _ctx(
            _SEED, i, note="embeddings data count mismatch",
            got=len(data), want=len(parsed.input_items),
        )
        for position, entry in enumerate(data):
            assert entry["object"] == "embedding", _ctx(
                _SEED, i, note="data entry object not 'embedding'", pos=position
            )
            # index equals its input position => monotonically increasing by 1.
            assert entry["index"] == position, _ctx(
                _SEED, i, note="embeddings index not in input position order",
                got=entry["index"], want=position,
            )
            # the vector round-trips byte-identically (as a list of floats).
            assert entry["embedding"] == list(vectors[position][1]), _ctx(
                _SEED, i, note="embedding vector not preserved", pos=position
            )

        # the index sequence is strictly monotonically increasing.
        indices = [entry["index"] for entry in data]
        assert indices == sorted(indices) and len(set(indices)) == len(indices), _ctx(
            _SEED, i, note="embeddings indices not strictly increasing", got=indices
        )


def test_property_2_moderations_roundtrip_preserves_items_and_model() -> None:
    rng = random.Random(_SEED ^ 0xA5A5)
    for i in range(_ITERATIONS):
        raw, model, items = _gen_moderations_body(rng)

        parsed = _PARSER.parse_moderations(raw)

        # --- Parsed input items preserved byte-for-byte, in request order (R2.2). ----
        assert parsed.input_items == items, _ctx(
            _SEED, i, note="moderations input items mismatch",
            got=parsed.input_items, want=items,
        )
        # optional model preserved exactly (present string or None).
        assert parsed.model == model, _ctx(
            _SEED, i, note="moderations model mismatch", got=parsed.model, want=model
        )

        # --- Map findings -> ModerationResult and render + re-parse (R2.2, R8.x). ----
        findings = _script_findings(rng)
        result = moderation_result_from_findings(findings)
        assert isinstance(result, ModerationResult)

        echo_model = model if model is not None else "text-moderation-latest"
        rendered = _RENDERER.moderation_body(echo_model, result)
        reparsed = json.loads(rendered)

        assert str(reparsed["id"]).startswith("modr-"), _ctx(
            _SEED, i, note="moderation id missing modr- prefix", got=reparsed.get("id")
        )
        assert reparsed["model"] == echo_model, _ctx(
            _SEED, i, note="moderation model mismatch", got=reparsed.get("model")
        )

        results = reparsed["results"]
        assert len(results) == 1, _ctx(
            _SEED, i, note="moderation results not a single row", got=len(results)
        )
        row = results[0]

        # the category map round-trips exactly (names + flags + scores).
        assert row["categories"] == dict(result.categories), _ctx(
            _SEED, i, note="moderation categories not preserved",
            got=row.get("categories"),
        )
        assert row["category_scores"] == dict(result.category_scores), _ctx(
            _SEED, i, note="moderation category_scores not preserved",
            got=row.get("category_scores"),
        )

        # overall flagged is true exactly when at least one category is flagged.
        want_flagged = any(result.categories.values())
        assert row["flagged"] is want_flagged, _ctx(
            _SEED, i, note="moderation flagged inconsistent with categories",
            got=row.get("flagged"), want=want_flagged,
        )

        # every flagged category name is a declared moderation category (stock or the
        # firewall-specific EXTRA keyed by the Category spelling).
        allowed_names = frozenset(MODERATION_CATEGORY_MAP.values())
        for name, flagged in row["categories"].items():
            assert flagged is True, _ctx(
                _SEED, i, note="categories map carried a non-flagged entry", name=name
            )
            assert name in allowed_names, _ctx(
                _SEED, i, note="categories map carried an undeclared category", name=name
            )
