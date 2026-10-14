"""OpenAI misc JSON codec -- embeddings / models / moderations (GW16).

``edge/wire/openai_misc.py`` is the SIBLING of ``edge/wire/openai.py`` (the
shipped chat/completions codec): it owns the route-specific wire logic for the
three OpenAI-compatible *misc* surfaces -- ``POST /v1/embeddings``,
``GET /v1/models`` (+ ``GET /v1/models/{id}``), and ``POST /v1/moderations``. It
is a separate module -- not an extension of ``openai.py`` -- because
``check_sizes`` caps every module at ``MAX_MODULE_LINES = 800`` and ``openai.py``
is already near that bound; keeping the misc parse + render here holds each
``edge/wire`` module comfortably under the gate (Decision 3). ``edge/wire`` stays
the ONLY layer permitted to render the OpenAI wire shapes (the import-linter
layer order ``edge > admit > plan > detect > resolve > dispatch > egress > audit
> runtime > contracts > domain`` puts ``edge`` at the top).

**Reuses the shared OpenAI typing -- never redefined (R2.6).** The malformed-body
exception :class:`~gateway_v2.edge.wire.openai.MalformedRequest` and the declared
``MALFORMED_*`` value-codes are IMPORTED from ``edge/wire/openai.py`` and raised
by the misc parser; this module does NOT re-declare them, nor the GW15
``CanonicalRequest`` / ``OpenAIRequestParser`` / ``OpenAIResponseRenderer`` /
``fold_input_channels`` -- those are imported where needed.

**Wire logic only -- no security decision (R2.5).** As with ``openai.py``, the
misc parse + render is PURE over the request bytes: it performs no I/O, resolves
no plan, authenticates nothing, and makes no block / redact decision. Every
security decision lives one layer up in ``edge/openai_misc.py``'s per-surface
lifecycle. A body that is malformed or off the OpenAI-compatible contract raises
:class:`MalformedRequest` carrying a *declared value-code* -- never a raw
framework ``json.JSONDecodeError`` / ``KeyError``.

**No capacity literal, no module-level mutable.** The declared misc value-codes
are plain module-level strings (a value-code is a code, not a capacity-position
literal -- the serving bounds live in ``runtime/resources.py``); the value types
are frozen + slotted dataclasses; there is no module-level list / dict / set.

This module is landed in waves. Task 2.1 (this foundation) lands the two new
declared value-codes and the misc request/response value types; tasks 2.2
(``MiscRequestParser``), 2.3 (``fold_misc_input`` + ``_assert_misc_fold_total``),
and 2.4 (``MiscResponseRenderer`` + the moderation category map) fill the rest.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from gateway_v2.domain.category import Category
from gateway_v2.domain.channels import TEXT_CHANNELS
from gateway_v2.domain.finding import Finding, FindingStatus
from gateway_v2.edge.wire.openai import (
    MALFORMED_BODY,
    MALFORMED_NOT_AN_OBJECT,
    MALFORMED_WRONG_SHAPE,
    ChannelFoldError,
    MalformedRequest,
)

__all__ = (
    "MISSING_REQUIRED_PARAMETER",
    "MODEL_NOT_FOUND",
    "MODERATION_CATEGORY_MAP",
    "ChannelFoldError",
    "MiscEmbeddingsRequest",
    "MiscModerationsRequest",
    "MiscRequestParser",
    "MiscResponseRenderer",
    "ModerationResult",
    "fold_misc_input",
    "moderation_result_from_findings",
)


# --------------------------------------------------------------------------- #
# New declared misc value-codes (Decision 1) -- plain strings, not HTTP objects.
# --------------------------------------------------------------------------- #
#
# These two codes join the shared value-code discipline (like ``MALFORMED_BODY``
# in ``openai.py`` and ``domain/posture.py``): they are *codes*, NOT HTTP objects
# and NOT capacity-position literals. The handler maps each code to an
# OpenAI-shaped error through the single ``edge/errors`` ``Error_Envelope`` --
# task 3 adds the two matching ``_ENVELOPE`` entries
# (``missing_required_parameter`` -> ``(400, "invalid_request_error")`` and
# ``model_not_found`` -> ``(404, "not_found_error")``), so the stock SDK raises
# ``openai.BadRequestError`` / ``openai.NotFoundError`` respectively.

#: A misc request body is missing a required OpenAI-contract parameter -- e.g. a
#: ``/v1/moderations`` body with no ``input``. Raised by the misc parser as a
#: ``MalformedRequest(MISSING_REQUIRED_PARAMETER)`` (R2.4) and rendered as a
#: ``400 invalid_request_error`` so the stock SDK raises ``BadRequestError``.
MISSING_REQUIRED_PARAMETER = "missing_required_parameter"

#: A ``GET /v1/models/{id}`` retrieve names a model absent from the identity's
#: Allowed_Model_Catalog (R7.3). Rendered as a ``404 not_found_error`` so the
#: stock SDK raises ``openai.NotFoundError`` on ``client.models.retrieve(unknown)``.
MODEL_NOT_FOUND = "model_not_found"


# --------------------------------------------------------------------------- #
# Value types (Data Models) -- frozen + slotted, built once per request.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class MiscEmbeddingsRequest:
    """The internal value parsed from a ``POST /v1/embeddings`` body (R2.1, R3.2).

    * ``model`` -- the upstream embeddings model routing field (R2.1).
    * ``input_items`` -- one Scannable_Text per ``input`` element, in request
      order: a plain-string ``input`` yields a single item; a list ``input``
      yields one item per element, preserving index order (R3.2) so the rendered
      embeddings batch can round-trip each vector back to its input position
      (R6.2/R6.3).

    Frozen + slotted: an immutable value built once per request; ``input_items``
    is a tuple so the value type carries no mutable state. The parser landing in
    task 2.2 packs this; task 2.4's renderer consumes it with the provider's
    vectors.
    """

    model: str
    input_items: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MiscModerationsRequest:
    """The internal value parsed from a ``POST /v1/moderations`` body (R2.2).

    * ``model`` -- the optional moderation model field; ``None`` when the body
      omits it (OpenAI's ``/v1/moderations`` ``model`` is optional, unlike
      embeddings), so the renderer echoes a default or the requested model.
    * ``input_items`` -- one Scannable_Text per ``input`` element, in request
      order (same string-or-list fold shape as embeddings). A body missing
      ``input`` is rejected by the parser with
      ``MalformedRequest(MISSING_REQUIRED_PARAMETER)`` (R2.4) rather than yielding
      an empty tuple here.

    Frozen + slotted; ``input_items`` is a tuple so there is no mutable state.
    """

    model: str | None
    input_items: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ModerationResult:
    """One ``POST /v1/moderations`` result row (R8.2-R8.4).

    The scan-only moderations surface maps the input findings onto the OpenAI
    moderation result shape:

    * ``flagged`` -- ``True`` iff at least one category (stock OR a
      firewall-specific EXTRA) is flagged (R8.3).
    * ``categories`` -- an immutable ``Mapping`` of moderation-category name ->
      flagged bool. Stock OpenAI categories map directly; a firewall-specific
      ``Category`` with no stock counterpart rides as an EXTRA keyed by the
      firewall ``Category`` spelling, so no firewall signal is silently dropped
      (R8.4). The mapping instance is immutable (built as a frozen mapping /
      ``MappingProxyType`` by the renderer in task 2.4).
    * ``category_scores`` -- an immutable ``Mapping`` of the same category names
      -> confidence score (the finding ``confidence``, or a floor for an EXECUTED
      finding with no confidence).

    Frozen + slotted; the two mappings are typed ``Mapping`` so the value type
    exposes read-only views and carries no mutable state.
    """

    flagged: bool
    categories: Mapping[str, bool]
    category_scores: Mapping[str, float]

# --------------------------------------------------------------------------- #
# Body keys + decode discipline (mirrors ``openai.py::OpenAIRequestParser``).
# --------------------------------------------------------------------------- #
#
# These are the OpenAI-compatible misc body field names -- plain string keys, not
# capacity-position literals. The decode discipline mirrors the shipped chat
# parser's ``_decode`` exactly (catch ``(ValueError, UnicodeDecodeError)`` ->
# ``MALFORMED_BODY``; top level not a dict -> ``MALFORMED_NOT_AN_OBJECT``). It is
# re-expressed here as a module-private helper rather than reaching into
# ``OpenAIRequestParser`` so the chat parser is never redefined or sub-classed;
# the two parsers stay siblings that share the SAME value-codes (R2.6).

_BODY_MODEL_KEY = "model"
_BODY_INPUT_KEY = "input"


def _decode_object(body: bytes) -> Mapping[str, object]:
    """Decode ``body`` as a JSON object, or raise a declared value-code (R2.3).

    Mirrors ``openai.py::OpenAIRequestParser._decode``: a body that is not valid
    JSON (``json.loads`` raising ``ValueError``/``UnicodeDecodeError``) raises
    :data:`MALFORMED_BODY`; a body whose top level is not a JSON object raises
    :data:`MALFORMED_NOT_AN_OBJECT`. The raw framework decode error is caught and
    never propagated to the caller (R2.3).
    """
    try:
        decoded = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise MalformedRequest(MALFORMED_BODY) from exc
    if not isinstance(decoded, dict):
        raise MalformedRequest(MALFORMED_NOT_AN_OBJECT)
    return decoded


def _fold_input_items(obj: Mapping[str, object]) -> tuple[str, ...]:
    """Fold a present, well-formed ``input`` into ordered Scannable_Text items.

    The OpenAI-compatible ``input`` shape is a string (one item) OR a list of
    strings (one item per element, in request order -- R3.2). The caller MUST have
    already established that ``input`` is PRESENT (missing ``input`` is a
    surface-specific decision: embeddings raises :data:`MALFORMED_WRONG_SHAPE`,
    moderations raises :data:`MISSING_REQUIRED_PARAMETER` -- R2.4). A present
    ``input`` that is neither a string nor a list, or a list carrying a non-string
    element, raises :data:`MALFORMED_WRONG_SHAPE` -- never a raw framework error
    (R2.3).
    """
    value = obj[_BODY_INPUT_KEY]
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        items: list[str] = []
        for element in value:
            if not isinstance(element, str):
                raise MalformedRequest(MALFORMED_WRONG_SHAPE)
            items.append(element)
        return tuple(items)
    raise MalformedRequest(MALFORMED_WRONG_SHAPE)


# --------------------------------------------------------------------------- #
# MiscRequestParser (Components §1) -- SDK JSON body -> typed misc request.
# --------------------------------------------------------------------------- #


class MiscRequestParser:
    """SDK JSON body -> typed misc request. Pure; no I/O, no security decision.

    Sibling of :class:`~gateway_v2.edge.wire.openai.OpenAIRequestParser`: it owns
    the wire parse for the two POST misc surfaces (``/v1/embeddings`` and
    ``/v1/moderations``). Stateless (``__slots__ = ()``), so one shared instance
    is safe to reuse across every request. Each parse validates ONLY the
    OpenAI-compatible wire shape and extracts ``model`` + the ordered ``input``
    items; it performs no I/O, resolves no plan, authenticates nothing, and makes
    no block / redact decision (R2.5). Any shape failure raises
    :class:`MalformedRequest` with a declared value-code -- never a raw framework
    ``json.JSONDecodeError`` / ``KeyError`` / ``TypeError`` (R2.3).
    """

    __slots__ = ()

    def parse_embeddings(self, body: bytes) -> MiscEmbeddingsRequest:
        """Parse a ``POST /v1/embeddings`` body into a typed value (R2.1).

        ``model`` is REQUIRED and MUST be a string -- a missing or non-string
        ``model`` raises :data:`MALFORMED_WRONG_SHAPE` (embeddings has no dedicated
        missing-model code in this card; wrong-shape covers both cases). ``input``
        is REQUIRED: a string yields a single item; a list yields one item per
        element in request order (R3.2). A missing ``input``, a non-string /
        non-list ``input``, or a list carrying a non-string element raises
        :data:`MALFORMED_WRONG_SHAPE`. The parse is PURE and makes no security
        decision (R2.5); every failure is a declared :class:`MalformedRequest`
        (R2.3).
        """
        obj = _decode_object(body)
        if _BODY_MODEL_KEY not in obj:
            raise MalformedRequest(MALFORMED_WRONG_SHAPE)
        model = obj[_BODY_MODEL_KEY]
        if not isinstance(model, str):
            raise MalformedRequest(MALFORMED_WRONG_SHAPE)
        if _BODY_INPUT_KEY not in obj:
            raise MalformedRequest(MALFORMED_WRONG_SHAPE)
        input_items = _fold_input_items(obj)
        return MiscEmbeddingsRequest(model=model, input_items=input_items)

    def parse_moderations(self, body: bytes) -> MiscModerationsRequest:
        """Parse a ``POST /v1/moderations`` body into a typed value (R2.2).

        ``model`` is OPTIONAL -- an absent ``model`` yields ``None``; a present but
        non-string ``model`` raises :data:`MALFORMED_WRONG_SHAPE`. ``input`` is
        REQUIRED: a MISSING ``input`` raises
        :data:`MISSING_REQUIRED_PARAMETER` (R2.4 -- the specific code the stock SDK
        sees as a ``400 invalid_request_error``, not the generic malformed code). A
        present ``input`` folds the same way as embeddings (string -> one item;
        list of strings -> items in order; a non-string element or a
        non-string/non-list ``input`` raises :data:`MALFORMED_WRONG_SHAPE`). The
        parse is PURE and makes no security decision (R2.5); every failure is a
        declared :class:`MalformedRequest` (R2.3).
        """
        obj = _decode_object(body)
        model: str | None
        if _BODY_MODEL_KEY not in obj:
            model = None
        else:
            candidate = obj[_BODY_MODEL_KEY]
            if not isinstance(candidate, str):
                raise MalformedRequest(MALFORMED_WRONG_SHAPE)
            model = candidate
        if _BODY_INPUT_KEY not in obj:
            raise MalformedRequest(MISSING_REQUIRED_PARAMETER)
        input_items = _fold_input_items(obj)
        return MiscModerationsRequest(model=model, input_items=input_items)

# --------------------------------------------------------------------------- #
# Misc input-channel fold + import-time totality guard (C21, R3.2).
# --------------------------------------------------------------------------- #
#
# ``fold_misc_input`` is the CHANNEL-KEYED fold -- the misc-scope sibling of
# ``openai.py::fold_input_channels``. Where ``_fold_input_items`` (above) extracts
# the ordered ``input`` strings OUT of a decoded body, ``fold_misc_input`` takes
# those already-extracted items plus the channel NAME and emits the
# ``(channel_name, text)`` Scannable_Text pairs the surface's input scan iterates
# -- one pair per element, in request order (R3.2), parallel to GW15's
# ``fold_input_channels`` output shape.
#
# TOTALITY (C21), DISJOINT FROM GW15. ``_assert_misc_fold_total`` is the misc-scope
# twin of ``openai.py::_assert_fold_total``: the set of channel names this fold can
# emit (``_FOLDED_MISC_CHANNELS``) must equal the set of MISC-scoped
# ``TEXT_CHANNELS`` names (``_misc_channel_names``). A misc channel is one whose
# ``sdk_path`` begins with NEITHER ``messages[]`` (GW15's chat-input scope) NOR
# ``choices[]`` (the output scope) -- i.e. the ``input`` / ``input[]`` entries. The
# two totality checks never see each other's channels, so GW15's chat-scoped check
# stays untouched while this one covers the new misc inputs. ``ChannelFoldError``
# is REUSED from ``openai.py`` -- there is one fold-totality exception, not two.

#: The misc input channel names (match the ``TEXT_CHANNELS`` names task 1.1 added).
#: Named constants mirroring GW15's ``_CHANNEL_*`` so there is no bare literal at
#: the ``_FOLDED_MISC_CHANNELS`` set below.
_CHANNEL_EMBEDDINGS_INPUT = "embeddings_input"
_CHANNEL_MODERATIONS_INPUT = "moderations_input"

#: The channel names :func:`fold_misc_input` is used with. A frozenset (no
#: module-level mutable); the totality check below asserts this equals the set of
#: misc-scoped ``TEXT_CHANNELS`` names.
_FOLDED_MISC_CHANNELS = frozenset(
    (
        _CHANNEL_EMBEDDINGS_INPUT,
        _CHANNEL_MODERATIONS_INPUT,
    )
)

#: The ``TextChannel.sdk_path`` prefixes that mark a NON-misc channel: ``messages[]``
#: is GW15's chat-input scope and ``choices[]`` is the output scope. A misc channel
#: is one whose ``sdk_path`` begins with neither -- i.e. ``input`` / ``input[]``.
#: A tuple (not a mutable), so the misc scope is read off the authoritative
#: ``TEXT_CHANNELS`` tuple and never from a second hand-maintained list.
_NON_MISC_SDK_PATH_PREFIXES = ("messages[]", "choices[]")


def _misc_channel_names() -> frozenset[str]:
    """The set of misc-scoped ``TEXT_CHANNELS`` names (C21 scope boundary).

    A MISC channel is one whose ``sdk_path`` begins with NEITHER ``messages[]``
    (GW15's chat-input scope, which :func:`~gateway_v2.edge.wire.openai._input_channel_names`
    owns) NOR ``choices[]`` (the output scan's scope) -- i.e. the ``input`` /
    ``input[]`` entries task 1.1 added. Derived from the authoritative
    ``TEXT_CHANNELS`` tuple so there is one source of truth, and DISJOINT by scope
    from GW15's input-channel set.
    """
    return frozenset(
        channel.name
        for channel in TEXT_CHANNELS
        if not channel.sdk_path.startswith(_NON_MISC_SDK_PATH_PREFIXES)
    )


def fold_misc_input(items: tuple[str, ...], channel: str) -> tuple[tuple[str, str], ...]:
    """Fold ordered misc ``input`` items into channel-keyed pairs (C21, R3.2).

    Given the already-extracted, ordered ``items`` (from the parser's
    ``input_items``) and a channel NAME (``"embeddings_input"`` or
    ``"moderations_input"``), emit one ``(channel, item)`` Scannable_Text pair per
    element, in request order -- the misc-scope sibling of
    :func:`~gateway_v2.edge.wire.openai.fold_input_channels`. The
    EmbeddingsSurface / ModerationsSurface (task 5) call this to produce the
    scannable pairs their input scan iterates. The fold is PURE: no I/O, no
    security decision. It is TOTAL over the misc input channels -- the module-level
    :func:`_assert_misc_fold_total` guarantees every misc ``TextChannel`` has a
    folding path here (C21).
    """
    return tuple((channel, item) for item in items)


def _assert_misc_fold_total() -> None:
    """Fail the build if the misc fold is not total over the misc channels (C21).

    Compares the channel names :func:`fold_misc_input` is used with
    (:data:`_FOLDED_MISC_CHANNELS`) against the misc-scoped ``TEXT_CHANNELS`` names
    (:func:`_misc_channel_names`). Any divergence raises :class:`ChannelFoldError`
    at import, so a new misc input ``TextChannel`` with no misc folding path -- or
    a misc folding path naming a channel the enumeration dropped -- is a build-time
    failure rather than a field that silently escapes scanning. DISJOINT by scope
    from GW15's :func:`~gateway_v2.edge.wire.openai._assert_fold_total` (which keys
    on ``messages[]``); the two checks never see each other's channels.
    """
    expected = _misc_channel_names()
    if _FOLDED_MISC_CHANNELS != expected:
        missing = tuple(sorted(expected - _FOLDED_MISC_CHANNELS))
        extra = tuple(sorted(_FOLDED_MISC_CHANNELS - expected))
        raise ChannelFoldError(
            "misc-channel fold is not total over TEXT_CHANNELS "
            f"(unfolded misc channels: {missing}; folding paths with no "
            f"TEXT_CHANNELS entry: {extra})"
        )


_assert_misc_fold_total()

# --------------------------------------------------------------------------- #
# Moderation category mapping (Components §3, R8.3/R8.4) -- MappingProxyType.
# --------------------------------------------------------------------------- #
#
# The scan-only moderations surface maps the firewall ``Category`` taxonomy
# (``domain/category.py``) onto the OpenAI moderation ``categories{...}`` /
# ``category_scores{...}`` shape. Per the design (Components §3): the stock OpenAI
# moderation categories are a FIXED set (hate / harassment / sexual / violence /
# self-harm / ...). A firewall ``Category`` that maps to a stock category sets
# that stock category; a firewall-specific ``Category`` with NO stock counterpart
# rides as a category EXTRA keyed by the firewall ``Category`` spelling, flagged
# ``true``, so the stock set is never expanded in meaning and NO firewall signal
# is silently dropped (honest -- R8.4).
#
# The design does NOT assign any firewall ``Category`` to a stock OpenAI category
# (and is explicit: "when in doubt firewall categories ride as extras; do NOT
# invent stock-category mappings the design doesn't specify"). So EVERY firewall
# ``Category`` -- ``prompt_injection``, ``jailbreak``, ``pii``, ``secret``,
# ``credential``, ``command``, ``toxicity``, ``exfil``, ``dos`` -- currently rides
# as an EXTRA named by its own ``Category`` spelling (``Category`` is a
# ``StrEnum``, so its value IS the extra key). The mapping is a ``MappingProxyType``
# (an immutable read-only view -- no module-level mutable) from ``Category`` to the
# output moderation-category NAME; a stock mapping, if the design ever assigns one,
# is a one-line value change here (``Category.X: "hate"`` etc.) with no other code
# change -- the renderer reads the mapped name without caring whether it is stock
# or extra.
MODERATION_CATEGORY_MAP: Mapping[Category, str] = MappingProxyType(
    {
        Category.PROMPT_INJECTION: Category.PROMPT_INJECTION.value,
        Category.JAILBREAK: Category.JAILBREAK.value,
        Category.PII: Category.PII.value,
        Category.SECRET: Category.SECRET.value,
        Category.CREDENTIAL: Category.CREDENTIAL.value,
        Category.COMMAND: Category.COMMAND.value,
        Category.TOXICITY: Category.TOXICITY.value,
        Category.EXFIL: Category.EXFIL.value,
        Category.DOS: Category.DOS.value,
    }
)

#: The score placed in ``category_scores`` for an EXECUTED finding that carries no
#: ``confidence``. A ``Finding`` with ``status is EXECUTED`` always carries a
#: ``confidence`` (``Finding.__post_init__`` enforces it), so this floor is a
#: defensive default for the honest-max case the design names ("the finding
#: ``confidence``, or ``1.0`` for an EXECUTED finding with no confidence floor").
#: A plain score constant, not a capacity-position literal.
_EXECUTED_SCORE_FLOOR = 1.0


def moderation_result_from_findings(
    findings: Iterable[Finding],
) -> ModerationResult:
    """Map a scan's ``Finding`` set onto ONE OpenAI :class:`ModerationResult` (R8.3/R8.4).

    Each EXECUTED finding (``status is FindingStatus.EXECUTED``) sets its mapped
    moderation category to flagged ``True`` in ``categories`` and places its score
    (the finding ``confidence``, or :data:`_EXECUTED_SCORE_FLOOR` when a confidence
    is somehow absent) in ``category_scores`` -- a stock OpenAI category when the
    firewall ``Category`` maps to one, else a firewall-specific EXTRA keyed by the
    ``Category`` spelling (:data:`MODERATION_CATEGORY_MAP`). A non-EXECUTED finding
    (SKIPPED / UNAVAILABLE) does NOT flag a category (R8.3) -- but the SURFACE,
    not this pure mapping, is responsible for the fail-CLOSED ``scan_failure`` when
    the scan as a whole could not be computed (R8.6); this function only maps a
    set of findings it is GIVEN. When two EXECUTED findings share a mapped
    category, the higher score wins (the category is flagged either way, and the
    honest category_score is the strongest signal observed). Overall ``flagged``
    is ``True`` iff at least one category is flagged (R8.3). The returned mappings
    are immutable ``MappingProxyType`` views, so the :class:`ModerationResult`
    carries no mutable state. PURE: no I/O, no security decision (R2.5).
    """
    categories: dict[str, bool] = {}
    category_scores: dict[str, float] = {}
    for finding in findings:
        if finding.status is not FindingStatus.EXECUTED:
            continue
        name = MODERATION_CATEGORY_MAP[finding.category]
        score = finding.confidence
        if score is None:
            score = _EXECUTED_SCORE_FLOOR
        categories[name] = True
        existing = category_scores.get(name)
        if existing is None or score > existing:
            category_scores[name] = score
    flagged = any(categories.values())
    return ModerationResult(
        flagged=flagged,
        categories=MappingProxyType(dict(categories)),
        category_scores=MappingProxyType(dict(category_scores)),
    )


# --------------------------------------------------------------------------- #
# MiscResponseRenderer (Components §1) -- typed misc result -> ONE OpenAI body.
# --------------------------------------------------------------------------- #


@runtime_checkable
class _CatalogModelView(Protocol):
    """The minimal read-only shape the renderer needs of a catalog model.

    ``edge/app.py::CatalogModel`` (``id`` / ``created`` / ``owned_by``) satisfies
    this by structure. The renderer types against THIS structural protocol rather
    than importing ``CatalogModel`` from ``edge/app.py`` ON PURPOSE: ``edge/app.py``
    imports ``edge/routes.py`` which (once GW16 task 5 lands) imports
    ``edge/openai_misc.py`` -> this module, so an ``openai_misc.py ->
    edge/app.py`` import would close a cycle. Typing against a structural view
    keeps the dependency pointing the one safe direction (the surface, in
    ``edge/app`` / ``edge/openai_misc``, passes real ``CatalogModel`` instances
    DOWN into this ``edge/wire`` renderer) and keeps ``edge/wire`` free of any
    ``edge/app`` import, so no import cycle and no import-linter layer violation.
    """

    @property
    def id(self) -> str: ...

    @property
    def created(self) -> int: ...

    @property
    def owned_by(self) -> str: ...


#: Fixed OpenAI object tags + the deterministic moderation id. Plain strings, not
#: capacity-position literals (the serving bounds live in ``runtime/resources.py``).
#: The moderation id mirrors ``openai.py::DEFAULT_COMPLETION_ID`` -- a FIXED default
#: so the render is byte-stable and deterministic (the v1 conformance harness only
#: asserts the id STARTS WITH ``modr-``; a per-request-unique id, if ever needed,
#: is a SURFACE concern, never minted in this pure renderer).
_EMBEDDING_LIST_OBJECT = "list"
_EMBEDDING_OBJECT = "embedding"
_MODEL_OBJECT = "model"
_MODEL_LIST_OBJECT = "list"
_DEFAULT_MODERATION_ID = "modr-gateway"


class MiscResponseRenderer:
    """Typed misc result -> ONE OpenAI-compatible JSON body. Pure wire logic (R2.5).

    Stateless (``__slots__ = ()``), so a single shared instance is safe to reuse
    across every request -- the sibling of
    :class:`~gateway_v2.edge.wire.openai.OpenAIResponseRenderer`. Every body is
    serialised with a FIXED key order, ``ensure_ascii=True``, and compact
    ``(",", ":")`` separators (the exact GW15 idiom), so the same inputs always
    render the same bytes and a ``json.loads`` round-trip recovers the content.
    PURE: no I/O, no security decision (R2.5) -- the surface above has already
    resolved identity, run the scan, and (for embeddings) read the vectors from
    the injected provider; this renderer only places those exact values into the
    OpenAI JSON envelope.
    """

    __slots__ = ()

    def embeddings_body(
        self,
        req: MiscEmbeddingsRequest,
        vectors: tuple[tuple[int, tuple[float, ...]], ...],
    ) -> bytes:
        """Render the ``POST /v1/embeddings`` body with index order preserved (R6.2/R6.3).

        Emits ``{object:"list", data:[{index, embedding, object:"embedding"}],
        model, usage}`` -- one ``data`` entry per ``(index, vector)`` pair in
        ``vectors``, each entry's ``index`` taken from the pair (the input
        position) and the entries written in the order given, so the rendered
        batch round-trips each vector back to its input position (R6.3). The
        ``model`` echoes ``req.model``; the ``usage`` block is present with the
        shipped zero-token shape (the gateway invents no token accounting). Fixed
        key order + ``ensure_ascii=True`` + compact separators -> byte-stable.
        """
        data = [
            {
                "index": index,
                "embedding": list(vector),
                "object": _EMBEDDING_OBJECT,
            }
            for index, vector in vectors
        ]
        obj: dict[str, object] = {
            "object": _EMBEDDING_LIST_OBJECT,
            "data": data,
            "model": req.model,
            "usage": {
                "prompt_tokens": 0,
                "total_tokens": 0,
            },
        }
        body = json.dumps(obj, ensure_ascii=True, separators=(",", ":"))
        return body.encode("ascii")

    def models_list_body(self, models: Sequence[_CatalogModelView]) -> bytes:
        """Render the ``GET /v1/models`` list body in catalog order (R7.1).

        Emits ``{object:"list", data:[{id, object:"model", created, owned_by}]}``
        -- one ``data`` entry per catalog model, in the order given (catalog
        order), each in the OpenAI ``model`` shape. Typed against the structural
        :class:`_CatalogModelView` so ``edge/wire`` takes no ``edge/app`` import
        (the surface passes real ``CatalogModel`` instances). Fixed key order +
        ``ensure_ascii=True`` + compact separators -> byte-stable.
        """
        data = [self._model_object(model) for model in models]
        obj: dict[str, object] = {
            "object": _MODEL_LIST_OBJECT,
            "data": data,
        }
        body = json.dumps(obj, ensure_ascii=True, separators=(",", ":"))
        return body.encode("ascii")

    def model_retrieve_body(self, model: _CatalogModelView) -> bytes:
        """Render the ``GET /v1/models/{id}`` retrieve body (R7.2).

        Emits one ``{id, object:"model", created, owned_by}`` object for the
        resolved model, the same single-model shape the list body nests. Typed
        against :class:`_CatalogModelView` (no ``edge/app`` import). Fixed key
        order + ``ensure_ascii=True`` + compact separators -> byte-stable.
        """
        body = json.dumps(
            self._model_object(model), ensure_ascii=True, separators=(",", ":")
        )
        return body.encode("ascii")

    def moderation_body(self, model: str, result: ModerationResult) -> bytes:
        """Render the ``POST /v1/moderations`` body for ONE result (R8.2).

        Emits ``{id:"modr-...", model, results:[{flagged, categories{...},
        category_scores{...}}]}`` -- the deterministic :data:`_DEFAULT_MODERATION_ID`
        (byte-stable; the harness only asserts the ``modr-`` prefix), the echoed
        ``model``, and the single :class:`ModerationResult`'s ``flagged`` +
        category / score mappings. The category and score keys are written in
        SORTED order so the render is byte-stable regardless of the mapping's
        insertion order; the stock-vs-extra distinction is already baked into the
        key NAMES by :func:`moderation_result_from_findings` (R8.4). Fixed key
        order + ``ensure_ascii=True`` + compact separators -> byte-stable.
        """
        categories = {
            name: result.categories[name] for name in sorted(result.categories)
        }
        category_scores = {
            name: result.category_scores[name]
            for name in sorted(result.category_scores)
        }
        obj: dict[str, object] = {
            "id": _DEFAULT_MODERATION_ID,
            "model": model,
            "results": [
                {
                    "flagged": result.flagged,
                    "categories": categories,
                    "category_scores": category_scores,
                }
            ],
        }
        body = json.dumps(obj, ensure_ascii=True, separators=(",", ":"))
        return body.encode("ascii")

    @staticmethod
    def _model_object(model: _CatalogModelView) -> dict[str, object]:
        """Assemble ONE OpenAI ``model`` object (shared by list + retrieve).

        Fixed key order (``id`` / ``object`` / ``created`` / ``owned_by``) so list
        entries and the retrieve body render identically for the same model.
        """
        return {
            "id": model.id,
            "object": _MODEL_OBJECT,
            "created": model.created,
            "owned_by": model.owned_by,
        }
