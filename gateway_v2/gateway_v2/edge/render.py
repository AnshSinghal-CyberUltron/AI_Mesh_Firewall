"""Prometheus text renderer + its exact inverse parser (GW14d, task 3.1).

This is the ONE place a ``# TYPE`` line is produced. The six shipped producers
(``audit/metrics.py``, ``runtime/state_metrics.py``, ``state_control/metrics.py``,
``admit/metrics.py``, ``runtime/stream_metrics.py``, ``runtime/holdback_metrics.py``) emit
label-free value sets; the Publisher (task 11) merges them and hands families to this renderer so
every family carries a type and no family carries two.

Three rules, each inherited for a measured reason:

* **Type by the exact `audit/metrics.py` suffix rule.** :func:`metric_type` is the shipped
  ``"_total" ⇒ counter, else gauge`` rule, reused verbatim rather than re-derived, so the two
  surfaces can never drift. Histograms are the one shape the suffix cannot name, so they are typed
  ``histogram`` EXPLICITLY by the caller that knows a family is a histogram (R1.5, R11.1). R2-11
  (M4) measured untyped exposition landing as ``.../unknown`` and blinding a GKE HPA; an untyped or
  double-typed family is the defect, so :func:`render_families` emits exactly one ``# TYPE`` per
  family base name (R1.7).

* **Native Prometheus histograms with explicit finite bounds.** :func:`render_histogram` turns a
  shipped lifetime :class:`~gateway_v2.runtime.holdback_metrics.Histogram` (its ``samples`` list and
  ``total``) into ``_bucket``/``_sum``/``_count`` series. Each finite boundary ``b`` yields a
  cumulative ``_bucket{le="b"}`` = the count of samples ``≤ b``; the buckets are cumulative-monotone
  by construction; a terminal ``_bucket{le="+Inf"}`` equals ``_count``; ``_sum`` is Σsamples and
  ``_count`` is ``len(samples)`` (R1.6, R8.2). The bounds are PASSED IN by the caller — this module
  holds no capacity-position literal (the bounds derive from a producer's domain, chosen in the
  Publisher / contract).

* **The parser is the renderer's exact inverse (Property 8).** :func:`parse` reads a rendered body
  back into :class:`ParsedFamily` tuples, and re-rendering a parse result is byte-identical:
  ``render(parse(render(state))) == render(state)``. Values round-trip through a single canonical
  :func:`_format_value` so a second render is idempotent regardless of how the first value was
  spelled.

No HTTP object, no capacity literal, no module-level mutable — the module is a pure
value→text→value transform that ``edge`` (the top layer) may freely import from ``runtime`` below.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from gateway_v2.runtime.holdback_metrics import Histogram

__all__ = (
    "COUNTER",
    "GAUGE",
    "HISTOGRAM",
    "Family",
    "ParsedFamily",
    "family_of",
    "metric_type",
    "parse",
    "render_families",
    "render_histogram",
    "render_scalar",
)

COUNTER = "counter"
GAUGE = "gauge"
HISTOGRAM = "histogram"

_TYPE_PREFIX = "# TYPE "
_POSITIVE_INF = "+Inf"
_TOTAL_SUFFIX = "_total"


# --------------------------------------------------------------------------- #
# Data models (frozen + slotted per the design Data Models).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Family:
    """One rendered metric family: its base name, its declared type, and its series lines.

    A histogram's ``_bucket``/``_sum``/``_count`` series share ONE ``base`` and count as a single
    family, so :func:`render_families` emits one ``# TYPE`` line for all of them. ``lines`` are the
    fully rendered series lines WITHOUT the ``# TYPE`` line (that line is produced once per family
    by the renderer).
    """

    base: str
    type: str
    lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ParsedFamily:
    """A family read back out of rendered text, for the round-trip property.

    ``samples`` is the ordered ``(series_name_with_labels, value)`` pairs that followed the
    family's single ``# TYPE`` line. Re-rendering a :class:`ParsedFamily` through
    :func:`render_families` reproduces the input text byte-for-byte (Property 8).
    """

    base: str
    type: str
    samples: tuple[tuple[str, float], ...]


# --------------------------------------------------------------------------- #
# Typing — the exact audit/metrics.py suffix rule.
# --------------------------------------------------------------------------- #


def metric_type(name: str) -> str:
    """Prometheus type by suffix: ``_total`` ⇒ counter, everything else ⇒ gauge.

    The exact rule shipped in ``audit/metrics.py`` (reused, not re-derived, so the two surfaces
    cannot drift). Histograms are NOT named by this rule — a histogram family is typed
    :data:`HISTOGRAM` explicitly by the caller that knows the shape (R1.5, R11.1).
    """
    return COUNTER if name.endswith(_TOTAL_SUFFIX) else GAUGE


# --------------------------------------------------------------------------- #
# Value formatting — one canonical spelling so a second render is idempotent.
# --------------------------------------------------------------------------- #


def _format_value(value: float) -> str:
    """Render a numeric value canonically so parse→render is byte-stable.

    An integral float is spelled without a trailing ``.0`` (``3.0`` → ``3``), matching the shipped
    ``audit.render`` which emits ``int`` values bare; a non-integral float uses ``repr`` (round-trip
    exact for IEEE-754). Parsing a rendered token back to ``float`` and re-formatting it yields the
    same text, so :func:`render_families` is idempotent on a parse result (Property 8).
    """
    if value != value or value in (float("inf"), float("-inf")):  # NaN / ±Inf: repr is stable.
        return repr(value)
    as_int = int(value)
    if value == as_int:
        return str(as_int)
    return repr(value)


# --------------------------------------------------------------------------- #
# Scalar + histogram rendering.
# --------------------------------------------------------------------------- #


def render_scalar(name: str, value: float) -> list[str]:
    """A scalar family: the ``# TYPE`` line (typed by suffix) + its single series line.

    Returned as the two lines ``["# TYPE <name> <type>", "<name> <value>"]`` so a caller can build
    a :class:`Family` or emit them directly. The type is :func:`metric_type` (R1.5).
    """
    return [f"{_TYPE_PREFIX}{name} {metric_type(name)}", f"{name} {_format_value(value)}"]


def render_histogram(base: str, hist: Histogram, bounds: Sequence[int]) -> Family:
    """Render a native Prometheus histogram family from a shipped :class:`Histogram`.

    For each finite boundary ``b`` in ``bounds`` (deduplicated, ascending), emit
    ``<base>_bucket{le="b"}`` = the count of samples ``≤ b`` — cumulative, so the sequence is
    non-decreasing by construction (R8.2). A terminal ``<base>_bucket{le="+Inf"}`` equals
    ``<base>_count`` = ``len(hist.samples)``; ``<base>_sum`` = Σsamples (``hist.total``). The bounds
    are the caller's explicit finite set (this module holds no capacity literal); the ONLY infinite
    boundary is the single ``+Inf`` terminal (R1.6, R4.4).

    The returned :class:`Family` carries ``type`` :data:`HISTOGRAM` and the ``_bucket``/``_sum``/
    ``_count`` lines WITHOUT a ``# TYPE`` line — :func:`render_families` emits the one ``# TYPE``
    line for the shared base.
    """
    samples = hist.samples
    count = len(samples)
    ordered_bounds = _finite_bounds(bounds)
    lines: list[str] = []
    for bound in ordered_bounds:
        cumulative = sum(1 for sample in samples if sample <= bound)
        lines.append(f'{base}_bucket{{le="{bound}"}} {_format_value(cumulative)}')
    # Terminal +Inf bucket == _count (every sample is ≤ +Inf), closing the cumulative sequence.
    lines.append(f'{base}_bucket{{le="{_POSITIVE_INF}"}} {_format_value(count)}')
    lines.append(f"{base}_sum {_format_value(hist.total)}")
    lines.append(f"{base}_count {_format_value(count)}")
    return Family(base=base, type=HISTOGRAM, lines=tuple(lines))


def _finite_bounds(bounds: Sequence[int]) -> list[int]:
    """Deduplicated, ascending finite boundaries (the explicit, non-infinite set).

    Sorting + dedup makes the cumulative bucket sequence deterministic and monotone regardless of
    the order the caller supplied the bounds in; the ``+Inf`` terminal is added by the renderer, so
    this returns finite values only.
    """
    return sorted(set(bounds))


# --------------------------------------------------------------------------- #
# Family rendering — de-dup, one # TYPE per family, deterministic order.
# --------------------------------------------------------------------------- #


def render_families(families: Iterable[Family]) -> str:
    """Render families to one byte-stable Prometheus body: one ``# TYPE`` per family, sorted.

    De-dups by family ``base`` name so no family emits two ``# TYPE`` lines even when two producers
    expose the same base (R1.7) — the FIRST occurrence of a base wins and later duplicates of the
    same base are dropped. Families are emitted in ascending ``base`` order, and each family's
    series lines in the order supplied, so the output is deterministic and byte-stable for a given
    family set. Every family contributes exactly one ``# TYPE <base> <type>`` line immediately
    before its series lines (R1.4).

    The body ends with a trailing newline, matching the shipped ``audit.render``.
    """
    seen: dict[str, Family] = {}
    for family in families:
        if family.base not in seen:
            seen[family.base] = family
    lines: list[str] = []
    for base in sorted(seen):
        family = seen[base]
        lines.append(f"{_TYPE_PREFIX}{base} {family.type}")
        lines.extend(family.lines)
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# parse — the exact inverse of render_families (Property 8).
# --------------------------------------------------------------------------- #


def parse(text: str) -> list[ParsedFamily]:
    """Read a rendered body back into :class:`ParsedFamily` tuples (the round-trip inverse).

    A ``# TYPE <base> <type>`` line opens a family; the series lines that follow (until the next
    ``# TYPE`` line or end of text) are its ``samples``. Each series line is split on the LAST
    space into ``(series_name_with_labels, value)``; the value is read as ``float`` and will be
    re-spelled canonically by :func:`_format_value` on the next render, so
    ``render(parse(render(state))) == render(state)`` byte-for-byte (Property 8).

    Families are returned in the order their ``# TYPE`` lines appear, which — for text produced by
    :func:`render_families` — is already sorted by base, so re-rendering reproduces the same order.
    """
    families: list[ParsedFamily] = []
    base: str | None = None
    ftype: str = GAUGE
    samples: list[tuple[str, float]] = []
    for raw in text.split("\n"):
        if raw == "":
            continue
        if raw.startswith(_TYPE_PREFIX):
            if base is not None:
                families.append(
                    ParsedFamily(base=base, type=ftype, samples=tuple(samples))
                )
            base, ftype = _parse_type_line(raw)
            samples = []
            continue
        samples.append(_parse_series_line(raw))
    if base is not None:
        families.append(ParsedFamily(base=base, type=ftype, samples=tuple(samples)))
    return families


def family_of(parsed: ParsedFamily) -> Family:
    """Rebuild a renderable :class:`Family` from a :class:`ParsedFamily` (the parse → render edge).

    Each ``(series_name, value)`` sample is re-spelled with the canonical :func:`_format_value`, so
    ``render_families(map(family_of, parse(body)))`` reproduces ``body`` byte-for-byte for any body
    :func:`render_families` produced (Property 8). This is the one-line bridge the round-trip
    property uses, keeping the renderer's line format owned entirely by this module.
    """
    lines = tuple(f"{name} {_format_value(value)}" for name, value in parsed.samples)
    return Family(base=parsed.base, type=parsed.type, lines=lines)


def _parse_type_line(line: str) -> tuple[str, str]:
    """Split ``# TYPE <base> <type>`` into ``(base, type)``.

    The type is the last whitespace token; the base is everything between the prefix and the type,
    so a (hypothetical) base containing a space still round-trips. A malformed line with no type
    token defaults the type to :data:`GAUGE`, matching the suffix rule's non-``_total`` default.
    """
    body = line[len(_TYPE_PREFIX):]
    name, sep, ftype = body.rpartition(" ")
    if sep == "":
        return body, GAUGE
    return name, ftype


def _parse_series_line(line: str) -> tuple[str, float]:
    """Split ``<series_name_with_labels> <value>`` on the LAST space into ``(name, value)``.

    Splitting on the last space keeps any ``{le="..."}`` label block intact in the name. The value
    token is read as ``float`` so the next render re-spells it canonically (Property 8).
    """
    name, _sep, value = line.rpartition(" ")
    return name, float(value)
