"""Fixed-vocabulary rejection + posture counters (GW14d, task 7.1).

Two counter sets whose label domains are CLOSED by construction and whose series are kept
distinct from a Policy_BLOCK series. Each closes a measured R2-11 defect:

* **`rejected{status,reason}` — reason-separated, never merged (R6).** R2-11's M2 finding was
  that ``rejected_503`` collapsed every 503 cause into one number, so 9,241 kill-switch 503s read
  the same as 8 store-outage 503s. :class:`RejectionCounters` keeps one series per
  ``(status, reason)`` pair where ``reason`` is drawn SOLELY from the closed
  :data:`REASON_VOCAB` (the ``audit.record`` Admission_Reason vocabulary — ``shed`` / ``rejected``,
  reused, never re-spelled). A ``reason`` that is not in the vocabulary does NOT fall back to a
  merged or default series: the per-reason counters are preserved untouched and a
  ``publisher_vocab_error_total`` indicator is incremented so the coverage gap is itself observable
  (R6.5, R14.3). No ``reason`` value is ever tenant-, org-, or request-derived (R6.3).

* **Posture counters — separate from Policy_BLOCK (R7).** A fail-closed posture refusal is NOT a
  detection decision. R2-11 measured FAIL_CLOSED being read as a policy BLOCK.
  :class:`PostureCounters` exposes one counter per :data:`POSTURE_CODES` member (the five base
  ``domain.posture`` codes + the ten GW12 ``STREAM_*`` codes), named with the EXACT single-spelled
  code string — no alternative spelling (R7.4) — and under a prefix that shares no name with any
  Policy_BLOCK series (R7.3, R7.6, R14.2). A refusal increments only its own code's counter (R7.2);
  an unknown code surfaces a coverage-gap error indication rather than being silently dropped.

Both sets emit a REAL ZERO per bucket / per code from the very first exposition (R6.4, R7.5): the
state "no rejections of this cause" (series present, value 0) must be distinguishable from "cause
not instrumented" (series absent). The vocab-error / coverage-gap indicators are likewise real
zeros from construction.

The counter STATE is mutable (these are recorders, like the shipped ``AdmissionMetrics``), so these
are slotted plain classes rather than frozen dataclasses — matching the producer idiom. The
vocabulary tuples are frozen module constants. No capacity literal, no module-level mutable, no HTTP
object; ``edge`` (the top layer) may freely import ``audit.record`` and ``domain.posture`` below it.
"""

from __future__ import annotations

from gateway_v2.audit import record
from gateway_v2.domain import posture

__all__ = (
    "POSTURE_CODES",
    "POSTURE_PREFIX",
    "REASON_VOCAB",
    "REJECTED_PREFIX",
    "VOCAB_ERROR_SERIES",
    "COVERAGE_GAP_SERIES",
    "PostureCounters",
    "RejectionCounters",
    "VocabularyError",
)

# --------------------------------------------------------------------------- #
# Closed vocabularies — reused from the shipped single-spelling homes, never re-derived.
# --------------------------------------------------------------------------- #

REASON_VOCAB: tuple[str, ...] = (record.SHED, record.REJECTED)
"""The closed Admission_Reason set from ``audit.record`` (``shed`` / ``rejected``).

Reused verbatim so the ``rejected`` family's ``reason`` label can never carry a spelling the
audit records do not. This is the ONLY source of ``reason`` values — nothing tenant/org/request
derived ever reaches a label (R6.1, R6.3)."""

POSTURE_CODES: tuple[str, ...] = (
    posture.KILL_SWITCH_UNAVAILABLE,
    posture.SHARED_STATE_UNAVAILABLE,
    posture.PLAN_UNAVAILABLE,
    posture.BUDGET_UNAVAILABLE,
    posture.OVERLOAD_SHED,
    posture.STREAM_KILLED,
    posture.STREAM_INTER_CHUNK_TIMEOUT,
    posture.STREAM_IDLE_TIMEOUT,
    posture.STREAM_WRITE_TIMEOUT,
    posture.STREAM_MAX_DURATION,
    posture.STREAM_KEY_REVOKED,
    posture.STREAM_PLAN_CHANGED,
    posture.STREAM_SNAPSHOT_STALE,
    posture.STREAM_MALFORMED_UPSTREAM,
    posture.STREAM_BUFFER_UNAVAILABLE,
)
"""The five base ``domain.posture`` codes + the ten GW12 ``STREAM_*`` codes, in the one spelling
each. Imported — not hardcoded — so a code's spelling has exactly one home (the C37 join-failure
guard) and no alternative-spelling series can exist (R7.1, R7.4)."""

# A prefix that shares NO name with any Policy_BLOCK series: posture counters are a disposition of
# the admission/stream posture, not of the detection pipeline, so they must never be conflated with
# a `block` decision series (R7.3, R7.6, R14.2).
POSTURE_PREFIX = "amf_posture_refusals"
REJECTED_PREFIX = "amf_rejected"

VOCAB_ERROR_SERIES = "amf_publisher_vocab_error_total"
"""Incremented when an out-of-vocabulary ``reason`` is observed. The counted, exported alternative
to falling back to a merged/default rejection series (R6.5, R14.3). Real zero from construction."""

COVERAGE_GAP_SERIES = "amf_publisher_coverage_gap_total"
"""Incremented when a posture refusal carries a code outside :data:`POSTURE_CODES`. The counted,
exported alternative to silently dropping an unmapped code (R7.1, R14.3). Real zero from
construction."""


class VocabularyError(ValueError):
    """Raised for an out-of-vocabulary ``reason`` / unknown Posture_Code when the caller asks to
    surface the error directly.

    The counter indicators (:data:`VOCAB_ERROR_SERIES` / :data:`COVERAGE_GAP_SERIES`) are the
    ALWAYS-ON record of a coverage gap; this exception is the opt-in loud surface for a caller that
    wants to fail a test or a strict path on an out-of-vocab value. ``observe()`` increments the
    indicator unconditionally and raises only when asked to."""


def _reason_series_name(status: int, reason: str) -> str:
    """``amf_rejected{status="<status>",reason="<reason>"}`` — exactly two labels, both closed.

    ``status`` is an integer the admission path produces (e.g. 503); ``reason`` is a
    :data:`REASON_VOCAB` member. No third label and no tenant/org/request value can appear (R6.1,
    R6.3)."""
    return f'{REJECTED_PREFIX}{{status="{status}",reason="{reason}"}}'


# --------------------------------------------------------------------------- #
# RejectionCounters — reason-separated 503s (R6).
# --------------------------------------------------------------------------- #


class RejectionCounters:
    """One ``rejected{status,reason}`` counter per observed ``(status, reason)``, never merged.

    ``reason`` is confined to :data:`REASON_VOCAB`. A fresh instance already carries a real zero for
    every reason at the canonical 503 status (R6.4), so the first exposition distinguishes "no
    rejections of this cause" from "cause not instrumented". Out-of-vocab reasons increment
    :data:`VOCAB_ERROR_SERIES` and are NEVER folded into a merged ``rejected_503`` (R6.5).
    """

    __slots__ = ("_counts", "_vocab_errors")

    # The canonical status every reason is pre-seeded at, so a real zero exists from construction.
    # 503 is the admission-shed/refusal status the Admission_Reason vocabulary describes (C40); it
    # is a protocol constant, not a capacity literal.
    _SEED_STATUS = 503

    def __init__(self) -> None:
        # (status, reason) -> count. Seeded with a real zero for every vocabulary reason at the
        # canonical status so the series is present-with-zero from the first exposition (R6.4).
        self._counts: dict[tuple[int, str], int] = {
            (self._SEED_STATUS, reason): 0 for reason in REASON_VOCAB
        }
        self._vocab_errors: int = 0

    def observe(self, status: int, reason: str, *, raise_on_unknown: bool = False) -> None:
        """Increment the distinct ``rejected{status,reason}`` series by exactly one.

        An in-vocabulary ``reason`` increments its own ``(status, reason)`` series and no other
        (R6.2) — two distinct reasons produce two distinct series, never a merged one. An
        out-of-vocabulary ``reason`` increments :data:`VOCAB_ERROR_SERIES`, leaves every per-reason
        counter untouched (no merged/default fallback, R6.5), and raises :class:`VocabularyError`
        only when ``raise_on_unknown`` is set.
        """
        if reason not in REASON_VOCAB:
            self._vocab_errors += 1
            if raise_on_unknown:
                raise VocabularyError(
                    f"reason {reason!r} is not in the closed Admission_Reason vocabulary "
                    f"{REASON_VOCAB!r}; refusing to merge into a default rejection series",
                )
            return
        key = (status, reason)
        self._counts[key] = self._counts.get(key, 0) + 1

    def vocab_error_total(self) -> int:
        """The out-of-vocabulary rejection count (R6.5). Real zero from construction."""
        return self._vocab_errors

    def series(self) -> dict[str, float]:
        """The ``rejected`` family + the vocab-error indicator, each a real zero at first scrape.

        One ``amf_rejected{status,reason}`` series per observed ``(status, reason)`` plus the
        pre-seeded zero for every vocabulary reason at the canonical status, and the single
        :data:`VOCAB_ERROR_SERIES` counter (R6.4). Deterministic ``(status, reason)`` ordering keeps
        the output byte-stable.
        """
        out: dict[str, float] = {
            _reason_series_name(status, reason): float(count)
            for (status, reason), count in sorted(self._counts.items())
        }
        out[VOCAB_ERROR_SERIES] = float(self._vocab_errors)
        return out


# --------------------------------------------------------------------------- #
# PostureCounters — one per Posture_Code, distinct from Policy_BLOCK (R7).
# --------------------------------------------------------------------------- #


class PostureCounters:
    """One counter per :data:`POSTURE_CODES` member, named with the exact single-spelled code.

    A fresh instance carries a real zero for every code (R7.5). A refusal increments ONLY its own
    code's counter (R7.2) and touches no Policy_BLOCK series — the counters live under
    :data:`POSTURE_PREFIX`, which shares no name with any ``block`` disposition series (R7.3, R7.6,
    R14.2). An unknown code increments :data:`COVERAGE_GAP_SERIES` rather than being dropped.
    """

    __slots__ = ("_counts", "_coverage_gaps")

    def __init__(self) -> None:
        # code -> count, pre-seeded with a real zero for every known code (R7.5).
        self._counts: dict[str, int] = {code: 0 for code in POSTURE_CODES}
        self._coverage_gaps: int = 0

    def observe(self, code: str, *, raise_on_unknown: bool = False) -> None:
        """Increment this Posture_Code's own counter by exactly one; no other code moves (R7.2).

        A code outside :data:`POSTURE_CODES` increments :data:`COVERAGE_GAP_SERIES` (the counted,
        observable alternative to a silent drop) and raises :class:`VocabularyError` only when
        ``raise_on_unknown`` is set. No Policy_BLOCK series is ever touched (R7.3, R7.6).
        """
        if code not in self._counts:
            self._coverage_gaps += 1
            if raise_on_unknown:
                raise VocabularyError(
                    f"posture code {code!r} is not in the single-spelled POSTURE_CODES "
                    f"vocabulary; a coverage gap, not a silent drop",
                )
            return
        self._counts[code] += 1

    def coverage_gap_total(self) -> int:
        """The unknown-posture-code count (R7.1 coverage). Real zero from construction."""
        return self._coverage_gaps

    def series(self) -> dict[str, float]:
        """One ``amf_posture_refusals_<code>_total`` series per code + the coverage-gap indicator.

        Every code is present with a real zero from the first exposition (R7.5). The names carry the
        exact single-spelled ``domain.posture`` string and the ``_total`` suffix so the renderer
        types them as counters; none collides with a Policy_BLOCK series (R7.4, R14.2). Codes are
        emitted in :data:`POSTURE_CODES` order.
        """
        out: dict[str, float] = {
            f"{POSTURE_PREFIX}_{code}_total": float(self._counts[code]) for code in POSTURE_CODES
        }
        out[COVERAGE_GAP_SERIES] = float(self._coverage_gaps)
        return out
