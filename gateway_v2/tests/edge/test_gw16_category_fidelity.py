# Feature: chat-surface-gw16-misc, Property 14: Moderation findings map to categories with fidelity  # noqa: E501
# Validates: Requirements 8.2, 8.3, 8.4
"""Property 14 — moderation findings map to categories with fidelity.

House idiom: a seeded ``random.Random``, no ``hypothesis``, >=10,000 iterations.

*For any* firewall ``Finding`` set drawn over the FULL firewall ``Category`` taxonomy —
with a mix of EXECUTED and non-EXECUTED (``SKIPPED`` / ``UNAVAILABLE``) statuses, varied
``confidence``, and duplicate categories — mapping that set through
:func:`moderation_result_from_findings` yields a :class:`ModerationResult` whose
``categories`` / ``category_scores`` are *faithful* to the findings: no category is
dropped, mismapped, or invented. Concretely, the mapping satisfies the
category-fidelity contract (R8.2/R8.3/R8.4):

* **EXECUTED ⇒ flagged, scored, correctly mapped.** Every EXECUTED finding's firewall
  ``Category`` maps through :data:`MODERATION_CATEGORY_MAP` to its *declared* OpenAI
  moderation category name, that name is present in ``categories`` with value ``True``,
  and the same name is present in ``category_scores`` with a score equal to the
  strongest ``confidence`` observed for that mapped category among the EXECUTED findings
  (duplicates collapse to the max). (R8.2/R8.4)
* **non-EXECUTED ⇒ no category.** A ``SKIPPED`` / ``UNAVAILABLE`` finding never, on its
  own, flags a category: a mapped category appears in the result iff at least one
  EXECUTED finding mapped to it. (R8.3)
* **``flagged`` iff ≥1 category.** The overall ``flagged`` is ``True`` exactly when at
  least one category is flagged — i.e. exactly when at least one EXECUTED finding is
  present. (R8.3)
* **No invented / undeclared name.** Every rendered category name (in both
  ``categories`` and ``category_scores``) is a *value* of
  :data:`MODERATION_CATEGORY_MAP`; the mapping never invents a name outside the declared
  taxonomy, and ``categories`` carries no non-flagged (``False``) entry. (R8.4)

The generator is smart: it draws the firewall ``Category`` from the FULL taxonomy
(``tuple(Category)``), covers every ``FindingStatus``, emits duplicate categories and
mixed statuses on the same category (so the max-score collapse and the
"any-EXECUTED-flags" rule are both exercised), and spans the empty finding set (no
category, ``flagged`` false) through dense sets saturating the whole taxonomy.
"""

from __future__ import annotations

import random

from gateway_v2.domain.category import Category
from gateway_v2.domain.finding import Finding, FindingStatus, Span
from gateway_v2.edge.wire.openai_misc import (
    MODERATION_CATEGORY_MAP,
    moderation_result_from_findings,
)

_SEED = 0x16_16_000E
_ITERATIONS = 10_000

#: The FULL firewall ``Category`` taxonomy — the generator draws from all of it so the
#: fidelity contract is exercised over every declared category.
_CATEGORIES: tuple[Category, ...] = tuple(Category)

#: The declared OpenAI moderation category NAMES — the only names the mapping may ever
#: render (every rendered name must be one of these values).
_DECLARED_NAMES: frozenset[str] = frozenset(MODERATION_CATEGORY_MAP.values())

_NON_EXECUTED: tuple[FindingStatus, ...] = (
    FindingStatus.SKIPPED,
    FindingStatus.UNAVAILABLE,
)


def _ctx(seed: int, i: int, **extra: object) -> str:
    body = " ".join(f"{k}={v!r}" for k, v in extra.items())
    return f"seed={seed:#x} iter={i} {body}"


def _gen_finding(rng: random.Random) -> Finding:
    """One finding over the full taxonomy; EXECUTED carries a confidence, else None."""
    category = rng.choice(_CATEGORIES)
    if rng.random() < 0.6:
        return Finding(
            detector="d",
            detector_version="1",
            category=category,
            status=FindingStatus.EXECUTED,
            confidence=rng.uniform(0.0, 1.0),
            spans=(Span(0, 1),),
            evidence=None,
        )
    return Finding(
        detector="d",
        detector_version="1",
        category=category,
        status=rng.choice(_NON_EXECUTED),
        confidence=None,
        spans=(),
        evidence=None,
    )


def _gen_findings(rng: random.Random) -> tuple[Finding, ...]:
    """A random finding set — spans the empty set through dense, duplicate-heavy sets."""
    return tuple(_gen_finding(rng) for _ in range(rng.randint(0, 12)))


def _expected_from_findings(
    findings: tuple[Finding, ...],
) -> tuple[dict[str, bool], dict[str, float]]:
    """The faithful expectation computed independently of the mapping under test.

    EXECUTED findings flag their mapped category and contribute their confidence; the
    score for a mapped category is the max confidence over the EXECUTED findings that
    map to it. non-EXECUTED findings contribute nothing.
    """
    categories: dict[str, bool] = {}
    scores: dict[str, float] = {}
    for finding in findings:
        if finding.status is not FindingStatus.EXECUTED:
            continue
        name = MODERATION_CATEGORY_MAP[finding.category]
        assert finding.confidence is not None  # EXECUTED invariant
        score = finding.confidence
        categories[name] = True
        existing = scores.get(name)
        if existing is None or score > existing:
            scores[name] = score
    return categories, scores


def test_property_14_moderation_category_fidelity() -> None:
    rng = random.Random(_SEED)
    for i in range(_ITERATIONS):
        findings = _gen_findings(rng)
        result = moderation_result_from_findings(findings)

        exp_categories, exp_scores = _expected_from_findings(findings)

        # --- Category set + flags are faithful: no category dropped, mismapped, or
        # --- invented, and non-EXECUTED findings never flag a category (R8.2/R8.3). --
        assert dict(result.categories) == exp_categories, _ctx(
            _SEED, i, note="categories not faithful to EXECUTED findings",
            got=dict(result.categories), want=exp_categories,
        )

        # --- Scores are faithful: each flagged category carries the max confidence
        # --- observed for it among the EXECUTED findings (R8.2/R8.4). ---------------
        assert dict(result.category_scores) == exp_scores, _ctx(
            _SEED, i, note="category_scores not faithful to EXECUTED findings",
            got=dict(result.category_scores), want=exp_scores,
        )

        # categories and category_scores cover exactly the same category names.
        assert set(result.categories) == set(result.category_scores), _ctx(
            _SEED, i, note="categories/category_scores key sets diverge",
            categories=sorted(result.categories),
            scores=sorted(result.category_scores),
        )

        # --- Every EXECUTED finding's Category is present + True with a score (R8.2). -
        for finding in findings:
            if finding.status is not FindingStatus.EXECUTED:
                continue
            name = MODERATION_CATEGORY_MAP[finding.category]
            assert result.categories.get(name) is True, _ctx(
                _SEED, i, note="EXECUTED finding did not flag its mapped category",
                category=finding.category, name=name,
            )
            assert name in result.category_scores, _ctx(
                _SEED, i, note="EXECUTED finding's mapped category has no score",
                category=finding.category, name=name,
            )

        # --- No undeclared category name is ever invented; no non-flagged entry ------
        # --- rides in categories (R8.4). --------------------------------------------
        for name, flagged in result.categories.items():
            assert flagged is True, _ctx(
                _SEED, i, note="categories carried a non-flagged (False) entry",
                name=name,
            )
            assert name in _DECLARED_NAMES, _ctx(
                _SEED, i, note="categories carried an undeclared category name",
                name=name,
            )
        for name in result.category_scores:
            assert name in _DECLARED_NAMES, _ctx(
                _SEED, i, note="category_scores carried an undeclared category name",
                name=name,
            )

        # --- Overall flagged is True iff at least one category is flagged (R8.3). ----
        want_flagged = len(exp_categories) > 0
        assert result.flagged is want_flagged, _ctx(
            _SEED, i, note="flagged inconsistent with flagged-category count",
            got=result.flagged, want=want_flagged,
        )
        assert result.flagged is any(result.categories.values()), _ctx(
            _SEED, i, note="flagged not equal to any(categories.values())",
            got=result.flagged,
        )

        # --- flagged iff at least one EXECUTED finding present (R8.3 cross-check). ----
        any_executed = any(f.status is FindingStatus.EXECUTED for f in findings)
        assert result.flagged is any_executed, _ctx(
            _SEED, i, note="flagged inconsistent with presence of EXECUTED findings",
            got=result.flagged, want=any_executed,
        )
