# Feature: chat-surface-gw16-misc, Property 3: Malformed misc body yields a declared value-code  # noqa: E501
# Validates: Requirements 2.3
"""Property 3 -- a malformed misc body yields a DECLARED value-code (fail-closed).

House idiom: a seeded ``random.Random``, no ``hypothesis``, >=10,000 iterations.

*For any* malformed / off-contract ``/v1/embeddings`` or ``/v1/moderations`` body,
:class:`~gateway_v2.edge.wire.openai_misc.MiscRequestParser` raises
:class:`~gateway_v2.edge.wire.openai.MalformedRequest` carrying one of the DECLARED
value-codes -- never a raw framework error (``json.JSONDecodeError`` / ``KeyError``
/ ``UnicodeDecodeError`` / ``ValueError`` / ``TypeError``), and never an undeclared
code string (R2.3). The parser is fail-closed: the ONLY exception it is permitted to
raise on a bad body is a ``MalformedRequest`` whose ``.code`` is a declared code.

Two invariants are checked for every generated malformed body, against BOTH parse
entry points (``parse_embeddings`` and ``parse_moderations``):

* **Declared code only.** The parser raises ``MalformedRequest`` and its ``.code`` is
  a member of the declared set (the shared ``MALFORMED_*`` codes plus the misc
  ``MISSING_REQUIRED_PARAMETER``) -- never an ad-hoc / undeclared string.
* **No raw framework error leaks.** No ``json.JSONDecodeError`` / ``ValueError`` /
  ``UnicodeDecodeError`` / ``KeyError`` / ``TypeError`` escapes the parse boundary.

The generator is a smart one: it walks the whole malformed input space the design
names -- not valid JSON, valid JSON whose top level is not an object, an empty
object, bodies with the required ``input`` absent, and bodies with ``input`` /
``model`` present but the wrong JSON type (number / bool / null / object / nested
list / list carrying a non-string element) -- plus truncated byte prefixes of
otherwise well-formed bodies, so partial / cut-off wire frames are covered too.

The ONE direction a generated body must NOT accidentally be WELL-formed is pinned by
construction: a well-formed embeddings body needs a string ``model`` AND a
string-or-string-list ``input``; a well-formed moderations body needs a present
string-or-string-list ``input``. Each generated case violates at least one of those,
so the parser is always on a malformed path and a raise is expected every iteration.
"""

from __future__ import annotations

import json
import random

import pytest

from gateway_v2.edge.wire.openai import (
    MALFORMED_BODY,
    MALFORMED_NOT_AN_OBJECT,
    MALFORMED_WRONG_SHAPE,
    MalformedRequest,
)
from gateway_v2.edge.wire.openai_misc import (
    MISSING_REQUIRED_PARAMETER,
    MODEL_NOT_FOUND,
    MiscRequestParser,
)

_SEED = 0x16_16_0003
_ITERATIONS = 10_000

#: One shared stateless parser -- ``__slots__ = ()`` so reuse across every case is
#: safe and mirrors the single shared boot instance.
_PARSER = MiscRequestParser()

#: The complete set of DECLARED value-codes a malformed misc body may carry. A
#: surfaced code NOT in this set is an undeclared (non-value) code -- the exact leak
#: R2.3 forbids. ``MODEL_NOT_FOUND`` is declared by the module but is a RETRIEVE-time
#: code (a handler concern), never raised by the pure parser; it is deliberately
#: EXCLUDED here so a parser that wrongly surfaces it fails this property.
_DECLARED_PARSE_CODES = frozenset(
    (
        MALFORMED_BODY,
        MALFORMED_NOT_AN_OBJECT,
        MALFORMED_WRONG_SHAPE,
        MISSING_REQUIRED_PARAMETER,
    )
)

#: Raw framework error types the parser must NEVER leak past its boundary (R2.3).
_RAW_FRAMEWORK_ERRORS = (
    json.JSONDecodeError,
    ValueError,
    UnicodeDecodeError,
    KeyError,
    TypeError,
)


def _ctx(i: int, **extra: object) -> str:
    body = " ".join(f"{k}={v!r}" for k, v in extra.items())
    return f"seed={_SEED:#x} iter={i} {body}"


# --------------------------------------------------------------------------- #
# Malformed-body generators -- the whole off-contract input space (R2.3).
# --------------------------------------------------------------------------- #

#: Wrong-typed JSON values used wherever a string / string-list is required. None of
#: these is a ``str`` and none is a list-of-``str``, so placing any of them at
#: ``model`` or ``input`` drives a wrong-shape failure.
_WRONG_TYPED_VALUES: tuple[object, ...] = (
    123,
    -1,
    0,
    3.14,
    True,
    False,
    None,
    {"nested": "object"},
    {},
    [1, 2, 3],
    [None],
    ["ok", 7],
    [["nested-list"]],
    [{"obj": "in-list"}],
    [True],
    [],  # an empty list IS a well-formed empty batch, so it is handled specially below
)


def _non_object_json(rng: random.Random) -> bytes:
    """Valid JSON whose top level is NOT an object -> ``MALFORMED_NOT_AN_OBJECT``."""
    value = rng.choice(
        (
            [1, 2, 3],
            ["a", "b"],
            "a top-level string",
            42,
            -7,
            3.5,
            True,
            False,
            None,
        )
    )
    return json.dumps(value).encode("utf-8")


def _not_json(rng: random.Random) -> bytes:
    """Bytes that are not valid JSON at all -> ``MALFORMED_BODY``."""
    choice = rng.randint(0, 5)
    if choice == 0:
        return b""
    if choice == 1:
        return b"this is not json"
    if choice == 2:
        return b'{"model": "m"'  # unterminated object
    if choice == 3:
        return b'{"model": "m"} trailing garbage'
    if choice == 4:
        return b"\xff\xfe\x00\x01"  # invalid utf-8 -> not decodable JSON
    return bytes(rng.randint(0, 255) for _ in range(rng.randint(1, 16)))


def _truncated_wellformed(rng: random.Random) -> bytes:
    """A proper byte-prefix of a well-formed body -> an unterminated JSON frame.

    A cut-off wire frame must still fail closed as ``MALFORMED_BODY`` (an invalid
    JSON document), never a raw decode error.
    """
    items = [f"item-{j}" for j in range(rng.randint(1, 3))]
    whole = json.dumps({"model": "m", "input": items}).encode("utf-8")
    if len(whole) <= 1:
        return b"{"
    cut = rng.randint(1, len(whole) - 1)
    return whole[:cut]


def _object_missing_input(rng: random.Random) -> bytes:
    """A JSON object with NO ``input`` key (model may be present/absent).

    Embeddings -> ``MALFORMED_WRONG_SHAPE`` (its missing-input code); moderations ->
    ``MISSING_REQUIRED_PARAMETER`` (R2.4). Either way a DECLARED code.
    """
    obj: dict[str, object] = {}
    if rng.random() < 0.6:
        obj["model"] = rng.choice(("text-embedding-3-small", "m", "o3"))
    if rng.random() < 0.3:
        obj["extra"] = rng.choice(("x", 1, True))
    return json.dumps(obj).encode("utf-8")


def _object_wrong_typed_input(rng: random.Random) -> bytes:
    """A JSON object whose ``input`` is present but the WRONG type.

    ``model`` is a valid string so the failure is unambiguously the ``input`` shape;
    the chosen value is never a plain ``str`` and never a list-of-``str`` with >=1
    element, so a wrong-shape raise is expected. (An empty list is a legal empty
    batch -- excluded via the retry loop below.)
    """
    while True:
        bad = rng.choice(_WRONG_TYPED_VALUES)
        if bad == []:
            continue  # a legal empty batch, not malformed -- skip
        break
    obj: dict[str, object] = {"model": "m", "input": bad}
    return json.dumps(obj).encode("utf-8")


def _object_wrong_typed_model(rng: random.Random) -> bytes:
    """A JSON object whose ``model`` is present but NOT a string.

    ``input`` is a valid string so the failure is unambiguously the ``model`` shape.
    For embeddings a non-string ``model`` is wrong-shape; for moderations a present
    non-string ``model`` is likewise wrong-shape (an ABSENT model is legal there, so
    we always keep ``model`` PRESENT and non-string).
    """
    bad_model = rng.choice(
        (123, 3.14, True, False, None, {"m": "obj"}, ["m"], [], {}),
    )
    obj: dict[str, object] = {"model": bad_model, "input": "a valid input string"}
    return json.dumps(obj).encode("utf-8")


def _empty_object() -> bytes:
    """``{}`` -- a well-formed JSON object with no required field present."""
    return b"{}"


def _gen_malformed(rng: random.Random) -> bytes:
    """Draw one malformed body uniformly across the whole off-contract space."""
    kind = rng.randint(0, 7)
    if kind == 0:
        return _not_json(rng)
    if kind == 1:
        return _non_object_json(rng)
    if kind == 2:
        return _truncated_wellformed(rng)
    if kind == 3:
        return _object_missing_input(rng)
    if kind == 4:
        return _object_wrong_typed_input(rng)
    if kind == 5:
        return _object_wrong_typed_model(rng)
    if kind == 6:
        return _empty_object()
    # A JSON object with a wrong-typed input AND no model -- still malformed.
    return json.dumps({"input": rng.choice((1, None, True, {}, [7]))}).encode("utf-8")


# --------------------------------------------------------------------------- #
# The property -- both parse entry points, declared-code-only, no raw leak.
# --------------------------------------------------------------------------- #


def _assert_declared_or_fail(
    parse: object,
    body: bytes,
    i: int,
    which: str,
) -> None:
    """Run one parse call; assert it fails closed with a DECLARED code (R2.3)."""
    try:
        result = parse(body)  # type: ignore[operator]
    except MalformedRequest as exc:
        assert exc.code in _DECLARED_PARSE_CODES, _ctx(
            i, which=which, note="undeclared value-code", code=exc.code, body=body
        )
        assert exc.code != MODEL_NOT_FOUND, _ctx(
            i, which=which, note="retrieve-only code leaked from parser", body=body
        )
        return
    except _RAW_FRAMEWORK_ERRORS as exc:  # pragma: no cover - a leak is a failure
        pytest.fail(
            _ctx(i, which=which, note="raw framework error leaked", exc=repr(exc), body=body)
        )
    pytest.fail(
        _ctx(i, which=which, note="malformed body did not raise", got=repr(result), body=body)
    )


def test_property_3_malformed_misc_body_yields_declared_value_code() -> None:
    rng = random.Random(_SEED)
    for i in range(_ITERATIONS):
        body = _gen_malformed(rng)
        # Both POST misc surfaces share the decode discipline; a malformed body must
        # fail closed with a declared code through EITHER entry point.
        _assert_declared_or_fail(_PARSER.parse_embeddings, body, i, which="embeddings")
        _assert_declared_or_fail(_PARSER.parse_moderations, body, i, which="moderations")


# --------------------------------------------------------------------------- #
# Pinned examples -- the two surface-specific missing-input codes (R2.4).
# --------------------------------------------------------------------------- #
#
# The property above asserts a DECLARED code; these two example tests pin the EXACT
# code each surface surfaces for a body missing ``input``, so the embeddings vs
# moderations divergence (wrong-shape vs missing-required-parameter) is caught at a
# named site if either code is renamed or the surfaces are made to agree wrongly.


def test_moderations_missing_input_is_missing_required_parameter() -> None:
    """A moderations body with no ``input`` -> ``MISSING_REQUIRED_PARAMETER`` (R2.4)."""
    with pytest.raises(MalformedRequest) as excinfo:
        _PARSER.parse_moderations(b'{"model": "m"}')
    assert excinfo.value.code == MISSING_REQUIRED_PARAMETER


def test_embeddings_missing_input_is_wrong_shape() -> None:
    """An embeddings body with no ``input`` -> ``MALFORMED_WRONG_SHAPE`` (declared)."""
    with pytest.raises(MalformedRequest) as excinfo:
        _PARSER.parse_embeddings(b'{"model": "m"}')
    assert excinfo.value.code == MALFORMED_WRONG_SHAPE


def test_empty_object_fails_closed_both_surfaces() -> None:
    """``{}`` fails closed with a declared code through both parse entry points."""
    with pytest.raises(MalformedRequest) as emb:
        _PARSER.parse_embeddings(b"{}")
    assert emb.value.code in _DECLARED_PARSE_CODES
    with pytest.raises(MalformedRequest) as mod:
        _PARSER.parse_moderations(b"{}")
    assert mod.value.code in _DECLARED_PARSE_CODES


def test_not_an_object_fails_closed_both_surfaces() -> None:
    """A top-level JSON array -> ``MALFORMED_NOT_AN_OBJECT`` through both surfaces."""
    with pytest.raises(MalformedRequest) as emb:
        _PARSER.parse_embeddings(b'["not", "an", "object"]')
    assert emb.value.code == MALFORMED_NOT_AN_OBJECT
    with pytest.raises(MalformedRequest) as mod:
        _PARSER.parse_moderations(b'["not", "an", "object"]')
    assert mod.value.code == MALFORMED_NOT_AN_OBJECT
