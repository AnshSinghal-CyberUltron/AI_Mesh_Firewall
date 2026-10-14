# Feature: chat-surface-gw16-misc, Task 2.11: malformed-body + firewall-extra unit tests
# Validates: Requirements 2.4, 8.4
"""Example-based (NOT property) unit tests over ``edge/wire/openai_misc`` (Task 2.11).

These are the NAMED REGRESSION ANCHORS the property suites
(``test_gw16_malformed.py`` Property 3, ``test_gw16_codec.py`` Property 14) cover
only probabilistically. Where those walk a random body / finding space, this file
pins the specific concrete body -> specific declared value-code mappings and the
specific firewall-extra rendering the design declares, as plain ``assert`` example
tests. Source is NOT modified; this is a NEW, standalone test module.

Two groups:

* **Malformed-body anchors (R2.4 / R2.3).** Concrete bodies -> the exact declared
  value-code each surface surfaces: a moderations body missing ``input`` ->
  ``MISSING_REQUIRED_PARAMETER``; an embeddings body missing ``input`` ->
  ``MALFORMED_WRONG_SHAPE``; a non-object JSON top level -> ``MALFORMED_NOT_AN_OBJECT``;
  non-JSON bytes -> ``MALFORMED_BODY``; the empty object ``{}``.
* **Firewall-extra anchors (R8.4 / R8.3).** The moderation response carries the
  firewall-specific category EXTRAs keyed by the firewall ``Category`` spelling via
  ``MODERATION_CATEGORY_MAP``; a benign (no EXECUTED finding) scan renders
  ``flagged=False`` with empty category maps; the EXTRA keys are EXACTLY the declared
  firewall ``Category`` spellings and do NOT collide with the stock OpenAI moderation
  category names.
"""

from __future__ import annotations

import json

import pytest

from gateway_v2.domain.category import Category
from gateway_v2.domain.finding import Finding, FindingStatus, Span
from gateway_v2.edge.wire.openai import (
    MALFORMED_BODY,
    MALFORMED_NOT_AN_OBJECT,
    MALFORMED_WRONG_SHAPE,
    MalformedRequest,
)
from gateway_v2.edge.wire.openai_misc import (
    MISSING_REQUIRED_PARAMETER,
    MODERATION_CATEGORY_MAP,
    MiscRequestParser,
    MiscResponseRenderer,
    ModerationResult,
    moderation_result_from_findings,
)

#: One shared stateless parser / renderer -- ``__slots__ = ()`` so reuse is safe and
#: mirrors the single shared boot instance.
_PARSER = MiscRequestParser()
_RENDERER = MiscResponseRenderer()

#: The stock OpenAI moderation category NAMES (the fixed public set). The design
#: separates stock names from firewall-specific EXTRAs, so none of the firewall
#: ``Category`` spellings used as EXTRA keys may collide with these.
_STOCK_OPENAI_MODERATION_CATEGORIES = frozenset(
    (
        "hate",
        "hate/threatening",
        "harassment",
        "harassment/threatening",
        "sexual",
        "sexual/minors",
        "violence",
        "violence/graphic",
        "self-harm",
        "self-harm/intent",
        "self-harm/instructions",
        "illicit",
        "illicit/violent",
    )
)


def _executed(category: Category, confidence: float) -> Finding:
    """Build one EXECUTED finding in ``category`` with ``confidence`` (flags it)."""
    return Finding(
        detector="unit-detector",
        detector_version="1",
        category=category,
        status=FindingStatus.EXECUTED,
        confidence=confidence,
        spans=(Span(start=0, end=1),),
        evidence=None,
    )


def _non_executed(category: Category, status: FindingStatus) -> Finding:
    """Build one non-EXECUTED finding (SKIPPED/UNAVAILABLE) -- confidence MUST be None."""
    return Finding(
        detector="unit-detector",
        detector_version="1",
        category=category,
        status=status,
        confidence=None,
        spans=(),
        evidence=None,
    )


# --------------------------------------------------------------------------- #
# Malformed-body anchors (R2.4 / R2.3) -- concrete body -> declared value-code.
# --------------------------------------------------------------------------- #


def test_moderations_missing_input_anchor() -> None:
    """``{"model":"m"}`` on moderations -> ``MISSING_REQUIRED_PARAMETER`` (R2.4)."""
    with pytest.raises(MalformedRequest) as excinfo:
        _PARSER.parse_moderations(b'{"model":"text-moderation-latest"}')
    assert excinfo.value.code == MISSING_REQUIRED_PARAMETER


def test_moderations_bare_empty_object_is_missing_input() -> None:
    """``{}`` on moderations -> ``MISSING_REQUIRED_PARAMETER`` (model optional, input not)."""
    with pytest.raises(MalformedRequest) as excinfo:
        _PARSER.parse_moderations(b"{}")
    assert excinfo.value.code == MISSING_REQUIRED_PARAMETER


def test_embeddings_missing_input_is_wrong_shape_anchor() -> None:
    """An embeddings body with ``model`` but no ``input`` -> ``MALFORMED_WRONG_SHAPE``."""
    with pytest.raises(MalformedRequest) as excinfo:
        _PARSER.parse_embeddings(b'{"model":"text-embedding-3-small"}')
    assert excinfo.value.code == MALFORMED_WRONG_SHAPE


def test_embeddings_empty_object_is_wrong_shape() -> None:
    """``{}`` on embeddings -> ``MALFORMED_WRONG_SHAPE`` (model required, both absent)."""
    with pytest.raises(MalformedRequest) as excinfo:
        _PARSER.parse_embeddings(b"{}")
    assert excinfo.value.code == MALFORMED_WRONG_SHAPE


def test_non_object_json_top_level_is_not_an_object_both_surfaces() -> None:
    """A valid JSON array top level -> ``MALFORMED_NOT_AN_OBJECT`` on both surfaces."""
    with pytest.raises(MalformedRequest) as emb:
        _PARSER.parse_embeddings(b'["x","y"]')
    assert emb.value.code == MALFORMED_NOT_AN_OBJECT
    with pytest.raises(MalformedRequest) as mod:
        _PARSER.parse_moderations(b'"a top-level string"')
    assert mod.value.code == MALFORMED_NOT_AN_OBJECT


def test_non_json_bytes_is_malformed_body_both_surfaces() -> None:
    """Bytes that are not JSON at all -> ``MALFORMED_BODY`` on both surfaces."""
    with pytest.raises(MalformedRequest) as emb:
        _PARSER.parse_embeddings(b"this is not json")
    assert emb.value.code == MALFORMED_BODY
    with pytest.raises(MalformedRequest) as mod:
        _PARSER.parse_moderations(b"\xff\xfe\x00\x01")
    assert mod.value.code == MALFORMED_BODY


# --------------------------------------------------------------------------- #
# Firewall-extra anchors (R8.4 / R8.3) -- declared EXTRA keys, benign result.
# --------------------------------------------------------------------------- #


def test_firewall_finding_rides_as_extra_keyed_by_category_spelling() -> None:
    """A firewall ``Category`` with no stock counterpart flags an EXTRA named by its
    own ``Category`` spelling (R8.4)."""
    result = moderation_result_from_findings(
        (_executed(Category.PROMPT_INJECTION, 0.91),)
    )
    assert result.flagged is True
    # The EXTRA key is EXACTLY the firewall Category spelling via the declared map.
    expected_key = MODERATION_CATEGORY_MAP[Category.PROMPT_INJECTION]
    assert expected_key == Category.PROMPT_INJECTION.value == "prompt_injection"
    assert result.categories[expected_key] is True
    assert result.category_scores[expected_key] == pytest.approx(0.91)
    # No stock category leaks in for a purely firewall-specific finding.
    assert set(result.categories) == {expected_key}


def test_extra_keys_are_exactly_the_firewall_category_spellings() -> None:
    """Every mapped moderation-category NAME is exactly the firewall ``Category`` value,
    and the map covers every ``Category`` member (R8.4)."""
    assert set(MODERATION_CATEGORY_MAP) == set(Category)
    for category, mapped_name in MODERATION_CATEGORY_MAP.items():
        assert mapped_name == category.value


def test_firewall_extra_keys_do_not_collide_with_stock_openai_categories() -> None:
    """The firewall EXTRA key spellings are disjoint from the stock OpenAI category
    names -- the design keeps stock and firewall-specific EXTRAs separate (R8.4)."""
    extra_keys = set(MODERATION_CATEGORY_MAP.values())
    assert extra_keys.isdisjoint(_STOCK_OPENAI_MODERATION_CATEGORIES)


def test_benign_scan_renders_flagged_false_with_empty_category_maps() -> None:
    """A scan with no EXECUTED finding -> ``flagged=False`` with empty category maps (R8.3)."""
    result = moderation_result_from_findings(
        (
            _non_executed(Category.PII, FindingStatus.SKIPPED),
            _non_executed(Category.TOXICITY, FindingStatus.UNAVAILABLE),
        )
    )
    assert result.flagged is False
    assert dict(result.categories) == {}
    assert dict(result.category_scores) == {}


def test_empty_findings_render_benign_result() -> None:
    """No findings at all -> a benign (``flagged=False``, empty maps) result (R8.3)."""
    result = moderation_result_from_findings(())
    assert result.flagged is False
    assert dict(result.categories) == {}
    assert dict(result.category_scores) == {}


def test_moderation_body_carries_firewall_extra_and_is_byte_stable() -> None:
    """The rendered moderation body carries the firewall EXTRA under its ``Category``
    spelling, renders ``flagged`` true, and is byte-stable (R8.2/R8.4)."""
    result = moderation_result_from_findings((_executed(Category.JAILBREAK, 0.77),))
    body = _RENDERER.moderation_body("text-moderation-latest", result)
    # Byte-stable: a second render of the same inputs is byte-identical.
    assert body == _RENDERER.moderation_body("text-moderation-latest", result)
    decoded = json.loads(body)
    assert decoded["id"].startswith("modr-")
    assert decoded["model"] == "text-moderation-latest"
    assert len(decoded["results"]) == 1
    row = decoded["results"][0]
    assert row["flagged"] is True
    jailbreak_key = Category.JAILBREAK.value
    assert row["categories"][jailbreak_key] is True
    assert row["category_scores"][jailbreak_key] == pytest.approx(0.77)
    # The EXTRA key is NOT a stock OpenAI moderation category name.
    assert jailbreak_key not in _STOCK_OPENAI_MODERATION_CATEGORIES


def test_benign_moderation_body_renders_empty_category_maps() -> None:
    """A benign result renders ``flagged=False`` and empty ``categories`` /
    ``category_scores`` objects in the wire body (R8.3)."""
    result = ModerationResult(
        flagged=False,
        categories={},
        category_scores={},
    )
    decoded = json.loads(_RENDERER.moderation_body("m", result))
    row = decoded["results"][0]
    assert row["flagged"] is False
    assert row["categories"] == {}
    assert row["category_scores"] == {}


def test_two_findings_same_category_keep_the_higher_score() -> None:
    """When two EXECUTED findings share a mapped category, the higher score wins and
    the category is flagged once (R8.3)."""
    result = moderation_result_from_findings(
        (
            _executed(Category.SECRET, 0.40),
            _executed(Category.SECRET, 0.85),
        )
    )
    key = Category.SECRET.value
    assert result.flagged is True
    assert result.categories[key] is True
    assert result.category_scores[key] == pytest.approx(0.85)
    assert set(result.categories) == {key}
