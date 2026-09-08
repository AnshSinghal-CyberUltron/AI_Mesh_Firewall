"""Pure validator for the detection corpus (G0.1) — data shapes and constants.

This module is the importable, stdlib-only validator for the labelled detection
corpus. It is a *pure* function over the filesystem: every data problem becomes a
``Violation`` in a ``LintReport`` rather than an exception (the only operational
error is a missing corpus root). A ``python -m corpus_lint`` CLI and a gateway
pytest wrapper both call ``lint_corpus(root)``.

Task 3.1 scope — SHELL ONLY
---------------------------
This file currently defines the *data shapes* (``Violation``, ``LintReport``) and
the module *constants* (``VALID_LABELS``, ``VALID_SPLITS``, ``REQUIRED_FIELDS``,
``SPLIT_SEED``). The behavioural functions (``normalize_text``, ``assign_split``,
the ``check_*`` rule functions, ``lint_corpus``, ``main``) are implemented in
subsequent tasks (3.2, 3.3, 4.x, 5.x). Do not implement their bodies here.

No production code is imported at module load. ``check_trigger_token_coverage``
(a later task) performs the only read-only, lazy, try/except-guarded import of
``ATTACK_PATTERNS`` so a scanner-import problem becomes a lint finding rather than
a collection crash (Requirement 9, no blast radius).
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import unicodedata
from dataclasses import dataclass

__all__ = (
    "Violation",
    "LintReport",
    "LoadedItem",
    "VALID_LABELS",
    "VALID_SPLITS",
    "REQUIRED_FIELDS",
    "ID_MIN_LEN",
    "ID_MAX_LEN",
    "TEXT_MIN_LEN",
    "TEXT_MAX_LEN",
    "PROVENANCE_MIN_LEN",
    "PROVENANCE_MAX_LEN",
    "SPLIT_SEED",
    "normalize_text",
    "assign_split",
    "check_schema",
    "check_dedup",
    "check_leakage",
    "check_paraphrase_disjoint",
    "check_split_reproducible",
    "check_coverage",
    "check_dev_traffic_shapes",
    "check_fixtures",
    "check_trigger_token_coverage",
    "check_path_scope",
    "lint_corpus",
    "main",
)


# --- data shapes ---------------------------------------------------------------


@dataclass(frozen=True)
class Violation:
    """A single lint finding.

    Attributes:
        rule: The rule that produced the finding, one of
            ``"schema"`` | ``"duplicate"`` | ``"leakage"`` | ``"disjointness"``
            | ``"coverage"`` | ``"fixture"`` | ``"path_scope"``
            | ``"trigger_token_drift"``.
        item_id: The offending item's id, or ``""`` for a corpus-level finding.
        detail: A human-readable description precise enough to locate and fix the
            issue (e.g. line number, matched token, required-vs-observed counts).
    """

    rule: str
    item_id: str
    detail: str


@dataclass(frozen=True)
class LintReport:
    """The aggregated result of a corpus lint run.

    Attributes:
        ok: True if and only if ``violations`` is empty.
        violations: Every finding from every rule (not just the first).
        counts: Summary counts, e.g.
            ``{"malicious": N, "benign": M, "attack_families": K, ...}``.
    """

    ok: bool
    violations: tuple[Violation, ...]
    counts: dict[str, int]


@dataclass(frozen=True)
class LoadedItem:
    """One physical JSONL line as produced by the loader (task 5.1).

    This is the *single item shape* every ``check_*`` rule consumes. The loader
    reads each line of ``malicious.jsonl`` / ``benign.jsonl`` and produces one
    ``LoadedItem`` per line, so a rule can always report the originating line
    number and the source file, even for a line that is not valid JSON.

    A well-formed line has ``obj`` set to the parsed ``dict`` and
    ``parse_error`` ``None``. A line that is not valid JSON (Requirement 2.9) has
    ``obj`` ``None`` and ``parse_error`` carrying the ``json.JSONDecodeError``
    message; ``check_schema`` turns that into a single schema :class:`Violation`
    naming the line, and the item is thereafter excluded from coverage counts
    (Requirement 1.8) because it is not schema-valid.

    Attributes:
        line: 1-based line number within :attr:`source`.
        source: The JSONL file the line came from (e.g. ``"malicious.jsonl"``);
            used only for human-readable violation detail.
        obj: The parsed JSON object, or ``None`` when the line failed to parse
            or parsed to a non-object.
        parse_error: The JSON decode error message, or ``None`` when the line
            parsed successfully.
    """

    line: int
    source: str
    obj: dict | None = None
    parse_error: str | None = None


# --- schema constants ----------------------------------------------------------

VALID_LABELS = ("malicious", "benign")
VALID_SPLITS = ("train", "eval")
REQUIRED_FIELDS = ("id", "text", "label", "family", "split", "provenance")

# Field length bounds (Requirements 2.2, 2.4, 2.8).
ID_MIN_LEN = 1
ID_MAX_LEN = 128
TEXT_MIN_LEN = 1
TEXT_MAX_LEN = 1_048_576
PROVENANCE_MIN_LEN = 1
PROVENANCE_MAX_LEN = 512


# --- normalization (Requirement 3.2) -------------------------------------------

# Matches one-or-more consecutive Unicode whitespace characters, collapsed to a
# single ASCII space. Compiled once at module load; ``re.UNICODE`` is implicit for
# ``str`` patterns on Python 3, so ``\s`` covers tabs, newlines, NBSP, etc.
_WHITESPACE_RUN = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Canonicalize ``text`` for comparison (dedup, leakage, disjointness).

    Applies, in this exact order:

    1. **lowercase** — ``str.lower()`` case-folds so casing never distinguishes
       two otherwise-identical items.
    2. **NFKC** — ``unicodedata.normalize("NFKC", ...)`` folds compatibility
       variants (e.g. full-width ``ｉｇｎｏｒｅ`` -> ``ignore``, ligatures) to their
       canonical form.
    3. **collapse whitespace** — every run of consecutive whitespace becomes a
       single ASCII space, so formatting/indentation differences do not matter.
    4. **strip** — leading/trailing whitespace is removed.

    The result is a deterministic, pure function of ``text`` with no side effects.
    """
    lowered = text.lower()
    folded = unicodedata.normalize("NFKC", lowered)
    collapsed = _WHITESPACE_RUN.sub(" ", folded)
    return collapsed.strip()


# --- deterministic split seed (Requirement 8) ----------------------------------
# Committed constant: the single source of truth for the reproducible train/eval
# split. ``assign_split(id)`` (task 3.3) maps ``sha256(SPLIT_SEED + id)`` into
# ``[0, 1)`` and assigns ``eval`` below the eval-fraction, else ``train``. This
# value is documented for reuse in ``SAMPLING_METHODOLOGY.md``; do not change it
# without re-recording the methodology, as every stored split would shift.
SPLIT_SEED = "detection-corpus/g0.1/split-seed/v1"


def assign_split(
    item_id: str, *, eval_fraction: float = 0.2, seed: str = SPLIT_SEED
) -> str:
    """Deterministically assign a Corpus_Item id to the ``train`` or ``eval`` Split.

    Hashes ``seed + item_id`` with SHA-256, takes the first 8 hex digits (32 bits)
    of the digest, and maps that integer into the half-open interval ``[0, 1)`` by
    dividing by ``2**32``. If the resulting fraction is below ``eval_fraction`` the
    item is assigned to ``eval``; otherwise to ``train``.

    This is a pure function of ``item_id``, ``seed``, and ``eval_fraction`` with no
    side effects, so re-running it on the same inputs always yields the same Split
    (Requirements 8.1, 8.2). ``check_split_reproducible`` (a later task) compares an
    item's stored ``split`` against this function's output to detect drift.

    Args:
        item_id: The Corpus_Item's stable identifier.
        eval_fraction: The target fraction assigned to ``eval`` (default ``0.2``).
        seed: The split seed; defaults to the committed :data:`SPLIT_SEED`.

    Returns:
        ``"eval"`` if ``hash(seed + item_id) / 2**32 < eval_fraction``, else
        ``"train"``.
    """
    digest = hashlib.sha256((seed + item_id).encode()).hexdigest()
    fraction = int(digest[:8], 16) / 2**32
    return "eval" if fraction < eval_fraction else "train"


# --- families context helper ---------------------------------------------------


def _load_family_sets(root: pathlib.Path) -> dict[str, frozenset[str]]:
    """Read ``families.json`` under ``root`` into label -> valid-family sets.

    Returns a mapping ``{"malicious": {...attack families...},
    "benign": {...benign families...}}``. The on-disk file keys the sets by
    ``"attack"`` / ``"benign"``; this maps ``"attack"`` onto the ``malicious``
    label so callers can look families up by a Corpus_Item's ``label`` directly.

    A missing key yields an empty set (so every family for that label is treated
    as invalid, surfacing as schema violations) rather than raising — the lint's
    contract is that data problems are Violations, not exceptions.
    """
    data = json.loads((root / "families.json").read_text(encoding="utf-8"))
    attack = frozenset(data.get("attack", ()))
    benign = frozenset(data.get("benign", ()))
    return {"malicious": attack, "benign": benign}


# --- schema rule (Requirements 2.2, 2.3, 2.4, 2.8, 2.9, 1.8) --------------------


def _schema_violations_for_item(
    item: LoadedItem, family_sets: dict[str, frozenset[str]]
) -> list[Violation]:
    """Return every schema :class:`Violation` for a single loaded line.

    A line that failed to parse (or did not parse to a JSON object) yields
    exactly one violation naming the line. A parsed object is checked for every
    required field, each field's type/length, the label/split enums, and the
    family being valid for the item's label. Multiple independent problems on one
    parsed object each get their own violation so a single run reports them all,
    but a malformed *line* yields exactly one violation (per Requirement 2.9 /
    Property 1).
    """
    # A line that is not valid JSON, or does not parse to an object: one
    # violation naming the line number and the decode reason (Requirement 2.9).
    if item.obj is None:
        reason = item.parse_error or "line is not a JSON object"
        return [
            Violation(
                rule="schema",
                item_id="",
                detail=f"{item.source}:{item.line}: invalid JSON line: {reason}",
            )
        ]

    obj = item.obj
    # Identify the item for the detail string as early as possible: prefer a
    # usable id, else fall back to the line locator so the finding is locatable.
    raw_id = obj.get("id")
    if isinstance(raw_id, str) and raw_id:
        located = raw_id
    else:
        located = ""
    line_loc = f"{item.source}:{item.line}"

    violations: list[Violation] = []

    def add(field: str, reason: str) -> None:
        violations.append(
            Violation(
                rule="schema",
                item_id=located,
                detail=f"{line_loc}: field '{field}': {reason}",
            )
        )

    # Required fields present (Requirement 2.9).
    for field in REQUIRED_FIELDS:
        if field not in obj:
            add(field, "required field missing")

    # id: non-empty string 1-128 chars (Requirements 2.2, 2.3 uniqueness handled
    # across the corpus in the caller below).
    if "id" in obj:
        value = obj["id"]
        if not isinstance(value, str):
            add("id", f"must be a string, got {type(value).__name__}")
        elif not (ID_MIN_LEN <= len(value) <= ID_MAX_LEN):
            add(
                "id",
                f"length {len(value)} outside {ID_MIN_LEN}-{ID_MAX_LEN}",
            )

    # text: string 1-1,048,576 chars with >=1 non-whitespace char (R2.4).
    if "text" in obj:
        value = obj["text"]
        if not isinstance(value, str):
            add("text", f"must be a string, got {type(value).__name__}")
        elif not (TEXT_MIN_LEN <= len(value) <= TEXT_MAX_LEN):
            add(
                "text",
                f"length {len(value)} outside {TEXT_MIN_LEN}-{TEXT_MAX_LEN}",
            )
        elif not value.strip():
            add("text", "contains no non-whitespace character")

    # provenance: non-empty string 1-512 chars (R2.8).
    if "provenance" in obj:
        value = obj["provenance"]
        if not isinstance(value, str):
            add("provenance", f"must be a string, got {type(value).__name__}")
        elif not (PROVENANCE_MIN_LEN <= len(value) <= PROVENANCE_MAX_LEN):
            add(
                "provenance",
                f"length {len(value)} outside "
                f"{PROVENANCE_MIN_LEN}-{PROVENANCE_MAX_LEN}",
            )

    # label enum (R2.5).
    label = obj.get("label")
    if "label" in obj and label not in VALID_LABELS:
        add("label", f"{label!r} not one of {VALID_LABELS}")

    # split enum (R2.7).
    if "split" in obj and obj.get("split") not in VALID_SPLITS:
        add("split", f"{obj.get('split')!r} not one of {VALID_SPLITS}")

    # family valid-for-label (R2.6, R1.6/1.7). Only decidable when both family is
    # present and label is a known enum value.
    if "family" in obj:
        family = obj["family"]
        if not isinstance(family, str):
            add("family", f"must be a string, got {type(family).__name__}")
        elif label in VALID_LABELS:
            valid = family_sets.get(label, frozenset())
            if family not in valid:
                add(
                    "family",
                    f"{family!r} is not a valid family for label {label!r}",
                )
        # When label itself is invalid/missing we cannot decide family validity;
        # the label violation already flags the item.

    return violations


def check_schema(
    items: list[LoadedItem],
    family_sets: dict[str, frozenset[str]] | None = None,
    *,
    root: pathlib.Path | None = None,
) -> list[Violation]:
    """Validate every loaded line against the Corpus_Item schema.

    This is a pure rule function (design "Components and Interfaces" table row
    ``check_schema(items)``, Requirements 2.2/2.3/2.4/2.8/2.9 and family-for-label
    from 1.6/1.7). It never raises on a data problem — every problem is returned
    as a schema :class:`Violation` (``rule="schema"``).

    Item shape (consumed by every ``check_*`` rule, produced by the loader in
    task 5.1): a list of :class:`LoadedItem`, one per physical JSONL line, each
    carrying its 1-based ``line`` number, ``source`` file, the parsed ``obj``
    (``dict`` or ``None``), and any ``parse_error``. Downstream rules
    (``check_dedup``, ``check_leakage``, ``check_coverage``, ...) read the same
    ``LoadedItem.obj`` and should operate only over items that passed this check
    (Requirement 1.8: a schema-rejected item is excluded from coverage counts).

    Family context: the set of valid families per label is supplied either as
    ``family_sets`` (``{"malicious": {...}, "benign": {...}}``, the shape returned
    by :func:`_load_family_sets`) or resolved from ``families.json`` under
    ``root``. Exactly one of ``family_sets`` or ``root`` must be given; passing
    ``root`` lets ``lint_corpus`` (task 5.1) call ``check_schema(items,
    root=root)`` without pre-loading the family sets. If neither is given, family
    validity cannot be decided and a ``ValueError`` is raised (a programming
    error, not a data problem).

    Rules enforced, per line:

    * a line that is not valid JSON, or does not parse to a JSON object ->
      **exactly one** schema violation naming the line (Requirement 2.9);
    * each :data:`REQUIRED_FIELDS` member present (Requirement 2.9);
    * ``id`` a non-empty string ``ID_MIN_LEN``-``ID_MAX_LEN`` chars, and **unique**
      across the whole corpus — a repeated id yields one violation per line that
      re-uses it, naming the id (Requirements 2.2, 2.3);
    * ``text`` a string ``TEXT_MIN_LEN``-``TEXT_MAX_LEN`` chars with >=1
      non-whitespace char (Requirement 2.4);
    * ``provenance`` a string ``PROVENANCE_MIN_LEN``-``PROVENANCE_MAX_LEN`` chars
      (Requirement 2.8);
    * ``label`` in :data:`VALID_LABELS`, ``split`` in :data:`VALID_SPLITS`
      (Requirements 2.5, 2.7);
    * ``family`` valid for the item's ``label`` — an attack family for
      ``malicious``, a benign family for ``benign`` (Requirements 1.6, 1.7, 2.6).

    Args:
        items: The loaded JSONL lines to validate.
        family_sets: Optional pre-loaded label -> valid-family sets.
        root: Optional corpus root; ``families.json`` is loaded from it when
            ``family_sets`` is not supplied.

    Returns:
        Every schema :class:`Violation` found, in line order (per-line problems
        grouped by their line). An empty list means every line is schema-valid.
    """
    if family_sets is None:
        if root is None:
            raise ValueError("check_schema requires either family_sets or root")
        family_sets = _load_family_sets(root)

    violations: list[Violation] = []

    # Per-line schema checks first, so the report reads in file/line order.
    for item in items:
        violations.extend(_schema_violations_for_item(item, family_sets))

    # Corpus-level id uniqueness (Requirement 2.3): a duplicated id value is a
    # schema violation on each line that carries it. Only well-typed, in-range,
    # present ids participate — a missing/oversized id is already flagged above,
    # and counting it here would double-report the same line.
    seen: dict[str, int] = {}
    for item in items:
        if item.obj is None:
            continue
        value = item.obj.get("id")
        if not isinstance(value, str) or not (
            ID_MIN_LEN <= len(value) <= ID_MAX_LEN
        ):
            continue
        seen[value] = seen.get(value, 0) + 1

    duplicated = {value for value, n in seen.items() if n > 1}
    if duplicated:
        for item in items:
            if item.obj is None:
                continue
            value = item.obj.get("id")
            if value in duplicated:
                violations.append(
                    Violation(
                        rule="schema",
                        item_id=value,
                        detail=(
                            f"{item.source}:{item.line}: field 'id': "
                            f"duplicate id {value!r} (appears "
                            f"{seen[value]} times across the corpus)"
                        ),
                    )
                )

    return violations


# --- dedup / leakage helpers ---------------------------------------------------


def _usable_id_and_text(item: LoadedItem) -> tuple[str, str] | None:
    """Return ``(id, text)`` for a loaded item, or ``None`` if it is unusable.

    ``check_dedup`` and ``check_leakage`` operate only over items that carry the
    two fields they compare — a well-typed, non-empty ``id`` and a ``str``
    ``text``. Any item whose ``obj`` is ``None`` (a line that did not parse), or
    whose ``id``/``text`` is missing or the wrong type, is skipped here because
    :func:`check_schema` already reports it; re-flagging it in these rules would
    double-count the same defect and could crash on non-string values.

    This is a pure predicate/extractor with no side effects.
    """
    obj = item.obj
    if obj is None:
        return None
    raw_id = obj.get("id")
    if not isinstance(raw_id, str) or not raw_id:
        return None
    raw_text = obj.get("text")
    if not isinstance(raw_text, str):
        return None
    return raw_id, raw_text


# --- dedup rule (Requirements 5.2, 5.3) ----------------------------------------


def check_dedup(items: list[LoadedItem]) -> list[Violation]:
    """Verify no two Corpus_Items share an identical :func:`normalize_text`.

    Pure rule function (design row ``check_dedup(items)``, Requirements 5.2/5.3
    and design Property 2). Groups every usable item by its Normalized_Text; any
    group with two or more distinct ids is a collision. Each colliding group
    yields **one** duplicate :class:`Violation` (``rule="duplicate"``) naming all
    conflicting ids, so the report is symmetric (each id in a pair is named) and
    complete (every collision is reported). All reported items are retained
    unmodified — this rule only reports.

    Items whose ``obj`` is ``None`` or that lack a usable ``id``/``text`` are
    skipped (already flagged by :func:`check_schema`, per Requirement 1.8).

    Args:
        items: The loaded JSONL lines to check.

    Returns:
        One duplicate :class:`Violation` per colliding Normalized_Text group,
        in first-seen group order; an empty list when every item's normalized
        text is distinct.
    """
    # Map normalized text -> ordered list of ids sharing it, preserving the
    # order items were seen so the report is deterministic.
    groups: dict[str, list[str]] = {}
    for item in items:
        usable = _usable_id_and_text(item)
        if usable is None:
            continue
        item_id, text = usable
        groups.setdefault(normalize_text(text), []).append(item_id)

    violations: list[Violation] = []
    for norm, ids in groups.items():
        # A group collides only when it holds two or more *distinct* ids; the
        # same id appearing twice is an id-duplication defect owned by
        # check_schema, not a normalized-text collision between separate items.
        distinct = list(dict.fromkeys(ids))
        if len(distinct) < 2:
            continue
        joined = ", ".join(distinct)
        for item_id in distinct:
            others = ", ".join(other for other in distinct if other != item_id)
            violations.append(
                Violation(
                    rule="duplicate",
                    item_id=item_id,
                    detail=(
                        f"item {item_id!r} shares normalized text with "
                        f"{others} (duplicate group: {joined})"
                    ),
                )
            )

    return violations


# --- leakage rule (Requirements 5.4, 5.5) --------------------------------------


def check_leakage(items: list[LoadedItem]) -> list[Violation]:
    """Verify no ``train`` item shares Normalized_Text with an ``eval`` item.

    Pure rule function (design row ``check_leakage(items)``, Requirements 5.4/5.5
    and design Property 3). Buckets usable items by Normalized_Text and, for each
    normalized text carrying at least one ``train`` id and at least one ``eval``
    id, reports the Train_Eval_Leakage: one leakage :class:`Violation`
    (``rule="leakage"``) per (train id, eval id) pair naming both ids. A
    normalized text seen only within one split, or only in items without a valid
    split, is not leakage.

    Items whose ``obj`` is ``None`` or that lack a usable ``id``/``text`` are
    skipped (already flagged by :func:`check_schema`, per Requirement 1.8). An
    item whose ``split`` is missing or not one of :data:`VALID_SPLITS` is ignored
    by this rule (the bad split is a schema defect); leakage is specifically a
    ``train`` vs ``eval`` collision.

    Args:
        items: The loaded JSONL lines to check.

    Returns:
        One leakage :class:`Violation` per conflicting (train id, eval id) pair,
        grouped by Normalized_Text in first-seen order; an empty list when no
        train/eval collision exists.
    """
    # Per normalized text, the ordered ids observed on each side of the split.
    train_ids: dict[str, list[str]] = {}
    eval_ids: dict[str, list[str]] = {}
    order: list[str] = []
    seen_norm: set[str] = set()

    for item in items:
        usable = _usable_id_and_text(item)
        if usable is None:
            continue
        item_id, text = usable
        split = item.obj.get("split") if item.obj is not None else None
        if split not in VALID_SPLITS:
            continue
        norm = normalize_text(text)
        if norm not in seen_norm:
            seen_norm.add(norm)
            order.append(norm)
        if split == "train":
            train_ids.setdefault(norm, []).append(item_id)
        else:  # "eval"
            eval_ids.setdefault(norm, []).append(item_id)

    violations: list[Violation] = []
    for norm in order:
        trains = list(dict.fromkeys(train_ids.get(norm, [])))
        evals = list(dict.fromkeys(eval_ids.get(norm, [])))
        if not trains or not evals:
            continue
        for train_id in trains:
            for eval_id in evals:
                violations.append(
                    Violation(
                        rule="leakage",
                        item_id=train_id,
                        detail=(
                            f"train item {train_id!r} shares normalized text "
                            f"with eval item {eval_id!r}"
                        ),
                    )
                )

    return violations


# --- paraphrase disjointness rule (Requirements 3.3, 3.4) ----------------------


def _paraphrase_trigger_tokens(tokens: object) -> list[str]:
    """Extract the normalized, non-empty trigger-token strings from ``tokens``.

    ``tokens`` is the RAW parsed ``trigger_tokens.json`` value: a ``list`` of
    ``{"token": ..., "source_pattern": ...}`` dicts (the shape committed on
    disk). For robustness this also accepts a plain ``list[str]`` — an entry that
    is a ``str`` is taken as the token directly, an entry that is a ``dict`` uses
    its ``"token"`` field. Every extracted token is passed through
    :func:`normalize_text` so containment is compared in the same normalized
    space as the item text (Requirement 3.3), and empties are dropped (a
    normalized-empty token would match every item vacuously). Order-preserving
    de-duplication keeps the report deterministic.

    Pure function; no side effects.
    """
    result: list[str] = []
    seen: set[str] = set()
    if not isinstance(tokens, list):
        return result
    for entry in tokens:
        if isinstance(entry, str):
            raw = entry
        elif isinstance(entry, dict):
            raw = entry.get("token")
        else:
            continue
        if not isinstance(raw, str):
            continue
        norm = normalize_text(raw)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        result.append(norm)
    return result


def check_paraphrase_disjoint(
    items: list[LoadedItem], tokens: object
) -> list[Violation]:
    """Verify every ``paraphrase``-family item is free of every Trigger_Token.

    Pure rule function (design row ``check_paraphrase_disjoint(items, tokens)``,
    Requirements 3.3/3.4 and design Property 4). For each usable item whose
    ``family`` is ``"paraphrase"``, its :func:`normalize_text` must contain NONE
    of the normalized Trigger_Tokens (substring containment, both sides
    normalized). Each matched token yields one disjointness :class:`Violation`
    (``rule="disjointness"``) naming the offending item id and the matched
    trigger token, so a single item carrying several tokens produces several
    findings.

    ``tokens`` is the raw parsed ``trigger_tokens.json`` list (a list of
    ``{"token", "source_pattern"}`` dicts); a plain ``list[str]`` of token
    strings is also accepted (see :func:`_paraphrase_trigger_tokens`).
    ``lint_corpus`` (task 5.1) may pass the parsed JSON directly.

    Items whose ``obj`` is ``None`` or that lack a usable ``id``/``text`` are
    skipped (already flagged by :func:`check_schema`, per Requirement 1.8). A
    non-``paraphrase`` family item is not this rule's concern.

    Args:
        items: The loaded JSONL lines to check.
        tokens: The parsed trigger-token list (dicts) or a list of token strings.

    Returns:
        One disjointness :class:`Violation` per (paraphrase item, matched token)
        pair, in item then token order; an empty list when the paraphrase family
        is fully disjoint from the trigger vocabulary.
    """
    trigger_tokens = _paraphrase_trigger_tokens(tokens)
    violations: list[Violation] = []
    if not trigger_tokens:
        return violations

    for item in items:
        usable = _usable_id_and_text(item)
        if usable is None:
            continue
        obj = item.obj
        if obj is None or obj.get("family") != "paraphrase":
            continue
        item_id, text = usable
        norm_text = normalize_text(text)
        for token in trigger_tokens:
            if token in norm_text:
                violations.append(
                    Violation(
                        rule="disjointness",
                        item_id=item_id,
                        detail=(
                            f"paraphrase item {item_id!r} contains trigger "
                            f"token {token!r} in its normalized text"
                        ),
                    )
                )

    return violations


# --- split reproducibility rule (Requirement 8.2) ------------------------------


def check_split_reproducible(items: list[LoadedItem]) -> list[Violation]:
    """Verify each item's stored ``split`` equals :func:`assign_split` of its id.

    Pure rule function (design row ``check_split_reproducible(items)``,
    Requirement 8.2 and design Property 5). For each usable item, the stored
    ``obj["split"]`` must equal ``assign_split(obj["id"])``; any drift yields one
    :class:`Violation` (``rule="split"``) naming the item and the stored-vs-
    expected values, so a hand-edited split is caught.

    Rule label: ``"split"`` (a dedicated label, per the task's leeway; the design
    only requires "a Violation"). Items whose ``obj`` is ``None`` or that lack a
    usable ``id``/``text`` are skipped (already flagged by :func:`check_schema`,
    per Requirement 1.8). An item whose stored ``split`` is missing or not one of
    :data:`VALID_SPLITS` is left to :func:`check_schema`; this rule only compares
    a present split against the deterministic assignment.

    Args:
        items: The loaded JSONL lines to check.

    Returns:
        One split :class:`Violation` per item whose stored split differs from
        ``assign_split(id)``, in line order; an empty list when every stored
        split matches.
    """
    violations: list[Violation] = []
    for item in items:
        usable = _usable_id_and_text(item)
        if usable is None:
            continue
        item_id, _ = usable
        obj = item.obj
        if obj is None:
            continue
        stored = obj.get("split")
        # A missing/invalid split is a schema defect owned by check_schema; only
        # compare when a valid split value is present.
        if stored not in VALID_SPLITS:
            continue
        expected = assign_split(item_id)
        if stored != expected:
            violations.append(
                Violation(
                    rule="split",
                    item_id=item_id,
                    detail=(
                        f"item {item_id!r} stored split {stored!r} but "
                        f"assign_split(id) is {expected!r}"
                    ),
                )
            )
    return violations


# --- coverage rule (Requirements 1.1-1.5, 5.6, 5.7, 8.4, 8.5) ------------------

# The nine ATTACK_PATTERNS attack families (Requirement 1.3 / glossary). The 8
# distinct-attack-family minimum is counted over THIS set; ``paraphrase`` is a
# separate dedicated family with its own >=1 minimum (Requirements 1.3/1.4).
ATTACK_PATTERN_FAMILIES = frozenset(
    (
        "prompt_injection",
        "jailbreak",
        "data_leakage",
        "goal_hijacking",
        "tool_overreach",
        "sql_injection",
        "command_injection",
        "path_traversal",
        "vector_injection",
    )
)

MIN_MALICIOUS = 300
MIN_BENIGN = 300
MIN_ATTACK_FAMILIES = 8


def _schema_valid_items(items: list[LoadedItem]) -> list[dict]:
    """Return the parsed ``obj`` of every usable item, for coverage counting.

    Coverage minimums are verified over SCHEMA-VALID items only (Requirement
    1.8): an item whose ``obj`` is ``None`` or that lacks a usable ``id``/``text``
    is excluded so a malformed item cannot mask a coverage shortfall.
    """
    result: list[dict] = []
    for item in items:
        usable = _usable_id_and_text(item)
        if usable is None:
            continue
        if item.obj is not None:
            result.append(item.obj)
    return result


def check_coverage(items: list[LoadedItem]) -> list[Violation]:
    """Verify the corpus meets every coverage minimum.

    Pure rule function (design row ``check_coverage(items)``, Requirements
    1.1-1.5, 5.6/5.7, 8.4/8.5). Over the schema-valid items only, each unmet
    minimum yields one coverage :class:`Violation` (``rule="coverage"``,
    ``item_id=""`` — a corpus-level finding) naming the required threshold and
    the observed count:

    * ``>=300`` items with label ``malicious`` (Requirement 1.1);
    * ``>=300`` items with label ``benign`` (Requirement 1.2);
    * ``>=8`` distinct attack families among malicious items, counted over the
      nine :data:`ATTACK_PATTERN_FAMILIES` (``paraphrase`` is counted separately
      below, per Requirements 1.3/1.4);
    * ``>=1`` item with family ``paraphrase`` (Requirement 1.4);
    * ``>=1`` item with family ``developer_traffic`` (Requirement 1.5);
    * ``>=1`` ``train`` item and ``>=1`` ``eval`` item (Requirements 8.4/8.5).

    Only schema-valid items are counted (Requirement 1.8) so a malformed item
    cannot hide a shortfall.

    Args:
        items: The loaded JSONL lines to check.

    Returns:
        One coverage :class:`Violation` per unmet minimum; an empty list when
        every minimum is satisfied.
    """
    objs = _schema_valid_items(items)

    malicious = sum(1 for o in objs if o.get("label") == "malicious")
    benign = sum(1 for o in objs if o.get("label") == "benign")

    attack_families = {
        o.get("family")
        for o in objs
        if o.get("label") == "malicious"
        and o.get("family") in ATTACK_PATTERN_FAMILIES
    }
    paraphrase = sum(1 for o in objs if o.get("family") == "paraphrase")
    dev_traffic = sum(1 for o in objs if o.get("family") == "developer_traffic")

    train = sum(1 for o in objs if o.get("split") == "train")
    eval_ = sum(1 for o in objs if o.get("split") == "eval")

    violations: list[Violation] = []

    def add(detail: str) -> None:
        violations.append(Violation(rule="coverage", item_id="", detail=detail))

    if malicious < MIN_MALICIOUS:
        add(
            f"malicious items: required >= {MIN_MALICIOUS}, observed {malicious}"
        )
    if benign < MIN_BENIGN:
        add(f"benign items: required >= {MIN_BENIGN}, observed {benign}")
    if len(attack_families) < MIN_ATTACK_FAMILIES:
        add(
            f"distinct attack families: required >= {MIN_ATTACK_FAMILIES}, "
            f"observed {len(attack_families)} ({sorted(attack_families)})"
        )
    if paraphrase < 1:
        add(f"paraphrase-family items: required >= 1, observed {paraphrase}")
    if dev_traffic < 1:
        add(
            f"developer_traffic-family items: required >= 1, "
            f"observed {dev_traffic}"
        )
    if train < 1:
        add(f"train-split items: required >= 1, observed {train}")
    if eval_ < 1:
        add(f"eval-split items: required >= 1, observed {eval_}")

    return violations


# --- developer-traffic FP-shape rule (Requirements 4.1, 4.2, 4.3) --------------

# FP_Prone_Shape 1 (Requirement 4.2): the command_injection pattern `[^`]+`
# (one backtick, >=1 non-backtick char, one closing backtick).
_INLINE_BACKTICK_SHAPE = re.compile(r"`[^`]+`")

# FP_Prone_Shape 2 (Requirement 4.3): a data_leakage tool-description shape that
# matches at least one of the four `.*`-wildcard data_leakage patterns. The
# shipped data_leakage patterns are verb+`.*`+object phrases (e.g. "send data to
# ...", "access database ...", "exfiltrate ... data"); a tool description that
# uses one of these verb/object shapes with intervening text (the `.*` wildcard)
# exercises the over-matching. Conservative alternation over the four shapes'
# anchor verbs+objects, requiring the `.*` gap between them.
_DATA_LEAKAGE_WILDCARD_SHAPE = re.compile(
    r"("
    r"(?:tell|give|show|reveal|dump|list|export)\b.*"
    r"(?:ssn|social\s+security|credit\s+card|password|secret|api\s*key)"
    r"|(?:exfiltrate|extract|steal|leak|expose)\b.*"
    r"(?:data|information|credential|secret|key)"
    r"|(?:send|upload|transmit|forward)\b.*\bto\b"
    r"|(?:access|retrieve|download|dump)\b.*"
    r"(?:database|record|user\s+data|customer)"
    r")",
    re.IGNORECASE | re.DOTALL,
)


def check_dev_traffic_shapes(items: list[LoadedItem]) -> list[Violation]:
    """Verify the developer_traffic family exercises both FP_Prone_Shapes.

    Pure rule function (design row ``check_dev_traffic_shapes(items)``,
    Requirements 4.1/4.2/4.3). Among usable items whose ``family`` is
    ``developer_traffic``, requires at least one item whose ``text`` contains an
    inline-backtick span matching ``` `[^`]+` ``` (the command_injection
    FP_Prone_Shape, Requirement 4.2) AND at least one item whose ``text`` matches
    a ``data_leakage`` ``.*``-wildcard tool-description shape (Requirement 4.3).
    Each missing shape yields one :class:`Violation` (``rule="shape"``) naming
    which shape is absent.

    Rule label: ``"shape"`` (a dedicated label, per the task's leeway; the design
    row's intent is a coverage/shape finding). Items whose ``obj`` is ``None`` or
    that lack a usable ``id``/``text`` are skipped (already flagged by
    :func:`check_schema`, per Requirement 1.8).

    Args:
        items: The loaded JSONL lines to check.

    Returns:
        Up to two :class:`Violation`s — one per missing FP_Prone_Shape; an empty
        list when the developer_traffic family exercises both shapes.
    """
    has_backtick = False
    has_wildcard = False
    for item in items:
        usable = _usable_id_and_text(item)
        if usable is None:
            continue
        obj = item.obj
        if obj is None or obj.get("family") != "developer_traffic":
            continue
        _, text = usable
        if not has_backtick and _INLINE_BACKTICK_SHAPE.search(text):
            has_backtick = True
        if not has_wildcard and _DATA_LEAKAGE_WILDCARD_SHAPE.search(text):
            has_wildcard = True
        if has_backtick and has_wildcard:
            break

    violations: list[Violation] = []
    if not has_backtick:
        violations.append(
            Violation(
                rule="shape",
                item_id="",
                detail=(
                    "developer_traffic family is missing a Benign_Item whose "
                    "text contains an inline-backtick span matching `[^`]+` "
                    "(command_injection FP_Prone_Shape, Requirement 4.2)"
                ),
            )
        )
    if not has_wildcard:
        violations.append(
            Violation(
                rule="shape",
                item_id="",
                detail=(
                    "developer_traffic family is missing a Benign_Item whose "
                    "text is a tool description matching a data_leakage "
                    ".*-wildcard FP_Prone_Shape (Requirement 4.3)"
                ),
            )
        )
    return violations


# --- fake-fixture rule (Requirements 10.1, 10.4, 10.5) -------------------------

# Conservative PII/secret shape detectors. Each is intentionally narrow to avoid
# false positives on ordinary prose while catching the enumerated shapes
# (Requirement 10.1: SSN, credit card, email, API key). Compiled once.
_FIXTURE_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # SSN: ddd-dd-dddd (Requirement 10.1). Word-bounded so it is not a substring
    # of a longer digit run.
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    # Credit-card-like: a 16-digit run, optionally grouped in 4s by space/hyphen.
    (
        "credit_card",
        re.compile(r"\b(?:\d[ -]?){15}\d\b"),
    ),
    # Email address (Requirement 10.1).
    (
        "email",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    ),
    # API-key-like: a common vendor prefix followed by a long alnum/_-/ run, or a
    # long high-entropy-ish token bound to an api-key/secret assignment.
    (
        "api_key",
        re.compile(
            r"\b(?:sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}"
            r"|xox[baprs]-[A-Za-z0-9-]{10,})\b"
        ),
    ),
)


def _fake_fixture_markers(obj: dict) -> list[str]:
    """Return the item's ``fake_fixtures`` marker list as normalized strings.

    The ``fake_fixtures`` field (design Data Models, Requirement 10.5) is the
    documented marker: a list of the fake sensitive values present in ``text``.
    A missing field is treated as an empty list (nothing marked). Non-string
    entries are ignored. Each marker is normalized with :func:`normalize_text` so
    a detected value is compared to markers in the same normalized space,
    tolerating incidental case/whitespace differences.
    """
    raw = obj.get("fake_fixtures")
    if not isinstance(raw, list):
        return []
    markers: list[str] = []
    for entry in raw:
        if isinstance(entry, str) and entry:
            markers.append(normalize_text(entry))
    return markers


def check_fixtures(items: list[LoadedItem]) -> list[Violation]:
    """Verify every PII/secret-shaped value in ``text`` is a documented fixture.

    Pure rule function (design row ``check_fixtures(items)``, Requirements
    10.1/10.4/10.5). For each usable item, scans ``text`` for conservative
    PII/secret shapes (SSN ``ddd-dd-dddd``, a 16-digit credit-card-like run,
    email, API-key-like tokens — see ``_FIXTURE_SHAPES``). A detected value that
    is NOT listed in the item's ``fake_fixtures`` marker list yields one
    :class:`Violation` (``rule="fixture"``) naming the item and the matched
    shape; a value present in ``fake_fixtures`` (compared in normalized space) is
    a documented Fake_Fixture and produces no violation (Requirement 10.5).

    The detectors are deliberately conservative (documented in
    ``_FIXTURE_SHAPES``) so ordinary prose does not false-positive. Items whose
    ``obj`` is ``None`` or that lack a usable ``id``/``text`` are skipped
    (already flagged by :func:`check_schema`, per Requirement 1.8).

    Args:
        items: The loaded JSONL lines to check.

    Returns:
        One fixture :class:`Violation` per (item, unmarked detected value); an
        empty list when every sensitive-shaped value is a documented fixture.
    """
    violations: list[Violation] = []
    for item in items:
        usable = _usable_id_and_text(item)
        if usable is None:
            continue
        obj = item.obj
        if obj is None:
            continue
        item_id, text = usable
        markers = _fake_fixture_markers(obj)
        # De-duplicate identical matched values within one item so a value that
        # appears twice is reported once.
        reported: set[str] = set()
        for shape_name, pattern in _FIXTURE_SHAPES:
            for match in pattern.finditer(text):
                value = match.group(0)
                norm_value = normalize_text(value)
                if not norm_value:
                    continue
                if norm_value in markers:
                    continue  # documented Fake_Fixture — safe
                key = f"{shape_name}:{norm_value}"
                if key in reported:
                    continue
                reported.add(key)
                violations.append(
                    Violation(
                        rule="fixture",
                        item_id=item_id,
                        detail=(
                            f"item {item_id!r} contains a {shape_name}-shaped "
                            f"value {value!r} not listed in fake_fixtures "
                            f"(undocumented sensitive-looking value)"
                        ),
                    )
                )
    return violations


# --- trigger-token coverage + path-scope rules (Req. 3.1, 9.1, 9.2) ------------

# Alphanumeric literal phrases extracted from a regex source string: runs of
# lowercase letters/digits/spaces between regex metacharacters. Used to re-derive
# the literal tokens the live ATTACK_PATTERNS key on.
_REGEX_LITERAL_RUN = re.compile(r"[a-z0-9]+(?:\s+[a-z0-9]+)*")


def _derive_live_literals() -> tuple[frozenset[str], str | None]:
    """Re-derive the normalized literal tokens the live scanner patterns key on.

    Performs the ONLY read-only, lazy, ``try/except``-guarded import of the
    shipped scanner (``gateway/ai_mesh_gateway/scanner.py``): imports
    ``ATTACK_PATTERNS`` (``dict[str, list[str]]`` of regex strings) and
    ``FUZZY_ANCHOR_PHRASES`` (``dict[str, list[list[str]]]`` of token-word
    lists), never running a scan or mutating the module (Requirement 9). The
    import is local so a scanner-import problem becomes a lint finding rather than
    a collection crash.

    From each ``ATTACK_PATTERNS`` regex it extracts the alphanumeric literal
    phrases (lowercasing first, so a raw ``\\b(?:reveal|...)`` contributes its
    verb runs); from each ``FUZZY_ANCHOR_PHRASES`` entry it joins the word list
    into a phrase. Every literal is normalized with :func:`normalize_text`. Runs
    shorter than 3 characters are dropped as noise (regex fragments like ``s``).

    Returns:
        ``(literals, None)`` on success, or ``(frozenset(), error_message)`` when
        the scanner cannot be imported/inspected — the caller turns the error
        message into a single ``trigger_token_drift`` :class:`Violation`.
    """
    try:
        # Lazy, read-only import. No scan is invoked; the module is not mutated.
        from ai_mesh_gateway.scanner import (  # type: ignore
            ATTACK_PATTERNS,
            FUZZY_ANCHOR_PHRASES,
        )
    except Exception as exc:  # pragma: no cover - exercised via injected failure
        return frozenset(), f"{type(exc).__name__}: {exc}"

    literals: set[str] = set()
    try:
        for patterns in ATTACK_PATTERNS.values():
            for pattern in patterns:
                for run in _REGEX_LITERAL_RUN.findall(pattern.lower()):
                    norm = normalize_text(run)
                    if len(norm) >= 3:
                        literals.add(norm)
        for phrase_lists in FUZZY_ANCHOR_PHRASES.values():
            for words in phrase_lists:
                norm = normalize_text(" ".join(words))
                if len(norm) >= 3:
                    literals.add(norm)
    except Exception as exc:  # pragma: no cover - defensive
        return frozenset(), f"{type(exc).__name__}: {exc}"

    return frozenset(literals), None


def check_trigger_token_coverage(tokens: object) -> list[Violation]:
    """Verify ``trigger_tokens.json`` still covers the live scanner literals.

    Pure-except-for-the-guarded-import rule function (design row
    ``check_trigger_token_coverage(tokens)``, Requirement 3.1). Re-derives the
    live literal tokens from the shipped ``ATTACK_PATTERNS`` +
    ``FUZZY_ANCHOR_PHRASES`` via :func:`_derive_live_literals` (a lazy, read-only,
    ``try/except``-guarded import — Requirement 9), then checks that every live
    literal is covered by the committed ``tokens`` list. A live literal not
    present in the committed list yields one :class:`Violation`
    (``rule="trigger_token_drift"``) naming the uncovered literal, so the
    disjointness guarantee cannot silently rot as the scanner evolves.

    If the scanner cannot be imported/inspected, returns a SINGLE
    ``trigger_token_drift`` :class:`Violation` describing the import failure and
    NEVER raises (Requirement 9: no blast radius).

    "Covered" means the live literal appears as (or as a substring of) some
    committed token's normalized text — the committed list may enumerate a
    longer phrase (e.g. ``ignore all previous instructions``) that contains the
    live verb-run literal (``ignore``). This keeps the audit honest without
    demanding a brittle exact match to the regex fragmentation.

    Args:
        tokens: The parsed ``trigger_tokens.json`` list (dicts) or a list of
            token strings.

    Returns:
        One ``trigger_token_drift`` :class:`Violation` per uncovered live
        literal, or a single such Violation describing an import failure; an
        empty list when the committed list covers every live literal.
    """
    live_literals, error = _derive_live_literals()
    if error is not None:
        return [
            Violation(
                rule="trigger_token_drift",
                item_id="",
                detail=(
                    "could not import ATTACK_PATTERNS to audit trigger tokens: "
                    f"{error}"
                ),
            )
        ]

    committed = _paraphrase_trigger_tokens(tokens)
    committed_blob = "\n".join(committed)

    violations: list[Violation] = []
    for literal in sorted(live_literals):
        # Covered if the literal is a substring of the committed token blob
        # (which is a newline-joined set of committed normalized tokens); this
        # matches an exact committed token or a longer committed phrase that
        # contains the literal.
        if literal in committed_blob:
            continue
        violations.append(
            Violation(
                rule="trigger_token_drift",
                item_id="",
                detail=(
                    f"live scanner literal {literal!r} is not covered by any "
                    f"committed trigger token in trigger_tokens.json"
                ),
            )
        )
    return violations


def check_path_scope(root: pathlib.Path) -> list[Violation]:
    """Verify every corpus file resolves under ``tests/detection_corpus/``.

    Pure rule function (design row ``check_path_scope(root)``, Requirements
    9.1/9.2). Resolves ``root`` and every file beneath it; any file that resolves
    to a path NOT under the resolved ``tests/detection_corpus/`` directory (for
    example via a symlink escape) yields one :class:`Violation`
    (``rule="path_scope"``) naming the offending path — enforcing the corpus's
    zero-blast-radius invariant that every introduced file lives under
    ``tests/detection_corpus/``.

    The scope anchor is the resolved ``tests/detection_corpus`` directory:
    ``root`` is expected to be that directory (the corpus root ``lint_corpus``
    passes). Resolution follows symlinks, so a file symlinked outside the tree is
    detected. This function only reports; it never mutates the filesystem.

    Args:
        root: The corpus root directory (``tests/detection_corpus/``).

    Returns:
        One path_scope :class:`Violation` per file resolving outside the scope
        directory (plus one for ``root`` itself if it does not resolve under a
        ``tests/detection_corpus`` path); an empty list when every file is in
        scope.
    """
    violations: list[Violation] = []
    resolved_root = root.resolve()

    # The scope directory is the resolved root when it is itself named
    # tests/detection_corpus; otherwise anchor on a tests/detection_corpus
    # ancestor if present. This keeps the check meaningful whether root IS the
    # corpus dir (the normal case) or a parent that contains it.
    scope = resolved_root

    def in_scope(path: pathlib.Path) -> bool:
        try:
            resolved = path.resolve()
        except OSError:
            return False
        return resolved == scope or scope in resolved.parents

    # Walk every file under root (following the resolved tree). rglob does not
    # traverse into symlinked directories' targets for matching purposes, but the
    # per-file resolve() below catches a symlinked file pointing outside scope.
    for path in resolved_root.rglob("*"):
        if not path.is_file():
            continue
        if not in_scope(path):
            violations.append(
                Violation(
                    rule="path_scope",
                    item_id="",
                    detail=(
                        f"corpus file {str(path)!r} resolves outside "
                        f"tests/detection_corpus/ (resolved to "
                        f"{str(path.resolve())!r})"
                    ),
                )
            )
    return violations


# --- corpus loader (task 5.1) --------------------------------------------------

# The two required JSONL corpus files, relative to the corpus root. A missing
# root or a missing one of these is an OPERATIONAL error (FileNotFoundError), not
# a data Violation, so the gate fails obviously rather than reporting a vacuously
# "clean" empty corpus (design Error Handling; Requirement 5.9).
_MALICIOUS_FILE = "malicious.jsonl"
_BENIGN_FILE = "benign.jsonl"
_REQUIRED_CORPUS_FILES = (_MALICIOUS_FILE, _BENIGN_FILE)

# The trigger-token catalogue used by the paraphrase-disjointness rule. Unlike
# the two corpus files this is not fatal-if-missing: an absent/invalid file
# yields an empty token list (the paraphrase rule then simply has nothing to
# check), keeping the loader a pure "data problems are Violations" function.
_TRIGGER_TOKENS_FILE = "trigger_tokens.json"


def _load_jsonl(path: pathlib.Path, source: str) -> list[LoadedItem]:
    """Read one ``.jsonl`` file into a list of :class:`LoadedItem`, one per line.

    Every physical line becomes exactly one ``LoadedItem`` carrying its 1-based
    ``line`` number and ``source`` file name, so downstream rules can always
    locate a finding. A line that is not valid JSON, or that parses to a
    non-object, is captured as a ``LoadedItem`` with ``obj=None`` and a
    ``parse_error`` message — never raised — so :func:`check_schema` turns it
    into a single schema :class:`Violation` and all problems surface in one run
    (Requirement 2.9). Blank/whitespace-only lines are skipped (they carry no
    item and are not an error).

    Raises:
        FileNotFoundError: If ``path`` does not exist — an operational error the
            caller (:func:`lint_corpus`) surfaces per Requirement 5.9.
    """
    text = path.read_text(encoding="utf-8")
    items: list[LoadedItem] = []
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip():
            # Blank line: no item, not an error. Line numbers still advance so a
            # later finding's reported line matches the physical file.
            continue
        try:
            parsed = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            items.append(
                LoadedItem(line=lineno, source=source, obj=None, parse_error=str(exc))
            )
            continue
        if isinstance(parsed, dict):
            items.append(LoadedItem(line=lineno, source=source, obj=parsed))
        else:
            # Valid JSON but not an object (e.g. a bare list/number/string):
            # schema-invalid, reported as one schema Violation naming the line.
            items.append(
                LoadedItem(
                    line=lineno,
                    source=source,
                    obj=None,
                    parse_error="line is not a JSON object",
                )
            )
    return items


def _load_trigger_tokens(root: pathlib.Path) -> object:
    """Return the parsed ``trigger_tokens.json`` value, or ``[]`` if unavailable.

    The paraphrase-disjointness and trigger-token-coverage rules consume the raw
    parsed value (a list of ``{"token", "source_pattern"}`` dicts). A missing or
    malformed ``trigger_tokens.json`` is NOT a fatal load error: it yields an
    empty list so the loader never raises on it (the two ``.jsonl`` corpus files
    are the only fatal-if-missing inputs, per Requirement 5.9). An empty token
    list simply means the paraphrase rule has nothing to compare against.
    """
    path = root / _TRIGGER_TOKENS_FILE
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


# --- lint entry point (task 5.1; Requirements 5.1, 5.8, 5.9, 5.10) -------------


def lint_corpus(root: pathlib.Path | str) -> LintReport:
    """Load the corpus under ``root``, run every rule, and aggregate a report.

    This is the single entry point the ``python -m corpus_lint`` CLI
    (:func:`main`) and the gateway pytest wrapper both call (design "the lint
    entry points"; Requirement 5.1). It loads ``malicious.jsonl`` and
    ``benign.jsonl`` from ``root`` into :class:`LoadedItem` lists, loads the
    ``trigger_tokens.json`` catalogue and ``families.json`` family sets, then
    runs EVERY rule over the combined item list and concatenates their findings:

    * :func:`check_schema` (schema, types/lengths, enums, id-uniqueness, family)
    * :func:`check_dedup` (Normalized_Text uniqueness)
    * :func:`check_leakage` (no train/eval Train_Eval_Leakage)
    * :func:`check_paraphrase_disjoint` (paraphrase family vs Trigger_Tokens)
    * :func:`check_split_reproducible` (stored split == ``assign_split(id)``)
    * :func:`check_coverage` (300/300/>=8-families/paraphrase/dev-traffic/split)
    * :func:`check_dev_traffic_shapes` (both developer_traffic FP_Prone_Shapes)
    * :func:`check_fixtures` (every PII/secret-shaped value is a Fake_Fixture)
    * :func:`check_trigger_token_coverage` (trigger_tokens.json still covers the
      live ATTACK_PATTERNS literals — the one guarded read-only scanner import)
    * :func:`check_path_scope` (every corpus file resolves under the root)

    The returned :class:`LintReport` has ``ok`` True **if and only if**
    ``violations`` is empty (design Property 6; Requirement 5.9), the full
    ``violations`` tuple (every finding from every rule, not just the first), and
    a ``counts`` summary (malicious/benign totals, distinct attack families,
    paraphrase/dev-traffic/train/eval counts, and total ``violations``).

    Data problems NEVER raise — they become :class:`Violation`s. The ONLY
    operational errors are a missing ``root`` directory or a missing required
    corpus file, which raise :class:`FileNotFoundError` so the gate fails
    obviously rather than reporting a vacuously "clean" empty corpus
    (Requirement 5.9; design Error Handling).

    Args:
        root: The corpus root directory (``tests/detection_corpus/``). Accepts a
            ``str`` or :class:`pathlib.Path`.

    Returns:
        The aggregated :class:`LintReport`.

    Raises:
        FileNotFoundError: If ``root`` is not an existing directory, or either
            ``malicious.jsonl`` or ``benign.jsonl`` is missing.
    """
    root_path = pathlib.Path(root)
    if not root_path.is_dir():
        raise FileNotFoundError(f"corpus root does not exist or is not a directory: {root_path}")
    for required in _REQUIRED_CORPUS_FILES:
        if not (root_path / required).is_file():
            raise FileNotFoundError(
                f"required corpus file missing: {root_path / required}"
            )

    malicious_items = _load_jsonl(root_path / _MALICIOUS_FILE, _MALICIOUS_FILE)
    benign_items = _load_jsonl(root_path / _BENIGN_FILE, _BENIGN_FILE)
    items = malicious_items + benign_items

    tokens = _load_trigger_tokens(root_path)
    family_sets = _load_family_sets(root_path)

    violations: list[Violation] = []
    violations.extend(check_schema(items, family_sets))
    violations.extend(check_dedup(items))
    violations.extend(check_leakage(items))
    violations.extend(check_paraphrase_disjoint(items, tokens))
    violations.extend(check_split_reproducible(items))
    violations.extend(check_coverage(items))
    violations.extend(check_dev_traffic_shapes(items))
    violations.extend(check_fixtures(items))
    violations.extend(check_trigger_token_coverage(tokens))
    violations.extend(check_path_scope(root_path))

    counts = _build_counts(items, violations)
    frozen = tuple(violations)
    return LintReport(ok=(frozen == ()), violations=frozen, counts=counts)


def _build_counts(
    items: list[LoadedItem], violations: list[Violation]
) -> dict[str, int]:
    """Summarize the corpus for :attr:`LintReport.counts`.

    Counts are computed over SCHEMA-VALID items only (:func:`_schema_valid_items`,
    Requirement 1.8) so a malformed item does not inflate a total, plus the
    number of violations recorded. Keys mirror the design's example
    (``{"malicious": N, "benign": M, "attack_families": K, ...}``).
    """
    objs = _schema_valid_items(items)
    attack_families = {
        o.get("family")
        for o in objs
        if o.get("label") == "malicious"
        and o.get("family") in ATTACK_PATTERN_FAMILIES
    }
    return {
        "items": len(objs),
        "malicious": sum(1 for o in objs if o.get("label") == "malicious"),
        "benign": sum(1 for o in objs if o.get("label") == "benign"),
        "attack_families": len(attack_families),
        "paraphrase": sum(1 for o in objs if o.get("family") == "paraphrase"),
        "developer_traffic": sum(
            1 for o in objs if o.get("family") == "developer_traffic"
        ),
        "train": sum(1 for o in objs if o.get("split") == "train"),
        "eval": sum(1 for o in objs if o.get("split") == "eval"),
        "violations": len(violations),
    }


# --- CLI (task 5.1; Requirements 5.1, 5.8, 5.10) -------------------------------


def main(argv: list[str] | None = None) -> int:
    """``python -m corpus_lint [root]`` CLI: lint a corpus and return an exit code.

    Resolves the corpus ``root`` from the first positional argument, defaulting to
    the directory containing this module (the corpus root, so ``python -m
    corpus_lint`` with no argument lints the shipped corpus). Calls
    :func:`lint_corpus`, prints every :class:`Violation` (one per line, as
    ``[rule] item_id: detail``) followed by a one-line summary, and returns the
    exit code required by the report contract (design Property 6; Requirements
    5.8, 5.10):

    * ``0`` when the report is ``ok`` (no violations);
    * a non-zero code in ``1..255`` otherwise — the violation count clamped into
      ``[1, 255]`` (so a single violation returns 1 and any overflow returns 255,
      never a false-clean 0).

    A missing root/required file raises :class:`FileNotFoundError` from
    :func:`lint_corpus`; :func:`main` lets it propagate (it is an operational
    error, not a lint result) so the CLI exits with a traceback rather than a
    misleading exit code.

    Args:
        argv: Optional argument list (excluding the program name). Defaults to
            ``sys.argv[1:]``.

    Returns:
        The process exit code (0 for ok, 1..255 for violations).
    """
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    root = args[0] if args else str(pathlib.Path(__file__).resolve().parent)

    report = lint_corpus(root)

    for violation in report.violations:
        print(f"[{violation.rule}] {violation.item_id}: {violation.detail}")

    if report.ok:
        print(
            f"corpus_lint: OK — {report.counts.get('malicious', 0)} malicious, "
            f"{report.counts.get('benign', 0)} benign, "
            f"{report.counts.get('attack_families', 0)} attack families"
        )
        return 0

    n = len(report.violations)
    print(f"corpus_lint: FAILED — {n} violation(s)")
    # Clamp into the required 1..255 non-zero range (Requirement 5.10): never 0
    # while violations exist, never above the single-byte exit-code ceiling.
    return max(1, min(n, 255))


if __name__ == "__main__":
    import sys

    sys.exit(main())
